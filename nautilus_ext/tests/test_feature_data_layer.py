"""
Tests for nautilus_ext Feature Data Layer.

All tests are Nautilus-free (no Cython required) except the ones marked
@pytest.mark.nautilus_required, which need compiled nautilus_trader.

Test coverage
-------------
Core types:
  1.  test_feature_event_construct_and_to_row
  2.  test_feature_event_from_row_round_trip
  3.  test_feature_schema_save_load_json
  4.  test_feature_schema_output_names

Engine protocol:
  5.  test_feature_engine_base_update_many_default
  6.  test_feature_engine_update_returns_none_for_unknown_type
  7.  test_mock_engine_implements_protocol

Registry:
  8.  test_feature_registry_register_and_build
  9.  test_feature_registry_build_from_dict
  10. test_feature_registry_available_includes_registered

Online store:
  11. test_online_store_put_get_latest
  12. test_online_store_get_window_n
  13. test_online_store_get_window_time_filter
  14. test_online_store_clear_specific_key

Offline store:
  15. test_offline_store_buffer_and_flush_writes_parquet
  16. test_offline_store_no_per_row_file_for_many_events
  17. test_offline_store_query_by_instrument_and_set
  18. test_offline_store_query_time_range
  19. test_offline_store_excludes_warmup_by_default
  20. test_offline_store_write_load_schema

Pipeline:
  21. test_feature_pipeline_update_no_dataframe
  22. test_feature_pipeline_warmup_marks_is_warmup
  23. test_feature_pipeline_warmup_then_live_continuity
  24. test_feature_pipeline_puts_to_online_store
  25. test_feature_pipeline_buffers_to_offline_store
  26. test_feature_pipeline_state_dict_round_trip

Context:
  27. test_strategy_runtime_context_get_value
  28. test_strategy_runtime_context_get_compat
  29. test_strategy_runtime_context_to_legacy_dict

ML layer:
  30. test_feature_dataset_load_returns_dataframe
  31. test_inference_context_feature_vector
  32. test_inference_context_is_ready

Integration:
  33. test_pipeline_runner_integration_with_mock_feed
  34. test_old_vwm_signal_engine_unaffected
  35. test_feature_joiner_join_df
  36. test_feature_query_cache_lru
  37. test_feature_checkpoint_save_load

VWM adapter (nautilus_required):
  38. test_vwm_bar_feature_engine_outputs_feature_event
  39. test_vwm_bar_feature_engine_non_bar_returns_none
  40. test_vwm_bar_feature_engine_state_dict_round_trip
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from nautilus_ext.features.feature_cache import FeatureQueryCache
from nautilus_ext.features.feature_checkpoint import FeatureCheckpointManager
from nautilus_ext.features.feature_engine import FeatureEngineBase
from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_joiner import FeatureJoiner
from nautilus_ext.features.feature_pipeline import FeaturePipeline
from nautilus_ext.features.feature_registry import (
    available_feature_engines,
    build_feature_engine,
    register_feature_engine,
)
from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec
from nautilus_ext.features.feature_store import OfflineFeatureStore, OnlineFeatureStore
from nautilus_ext.features.interfaces import StrategyRuntimeContext
from nautilus_ext.ml.feature_dataset import FeatureDatasetSpec, load_feature_dataset
from nautilus_ext.ml.inference_context import ModelInferenceContext
from nautilus_ext.strategies.interfaces.input_types import BarInput

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TS0 = 1_704_067_200_000   # 2024-01-01 00:00:00 UTC (ms)
_IID = "BTCUSDT-PERP.BINANCE"
_FSID = "test_features_v1"
_VER = "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fe(
    ts: int = _TS0,
    instrument_id: str = _IID,
    feature_set_id: str = _FSID,
    is_warmup: bool = False,
    **values,
) -> FeatureEvent:
    v = {"x": 1.0, "y": 2.0}
    v.update(values)
    return FeatureEvent(
        ts_event=ts,
        instrument_id=instrument_id,
        feature_set_id=feature_set_id,
        feature_version=_VER,
        values=v,
        is_warmup=is_warmup,
    )


def _make_schema(feature_set_id: str = _FSID) -> FeatureSetSpec:
    return FeatureSetSpec(
        feature_set_id=feature_set_id,
        version=_VER,
        input_types=["bar"],
        output_features=[
            FeatureFieldSpec("x", "float", description="feature x"),
            FeatureFieldSpec("y", "float", description="feature y"),
        ],
        description="Test feature set",
    )


def _make_bar(
    ts: int = _TS0,
    instrument_id: str = _IID,
    close: float = 100.0,
) -> BarInput:
    return BarInput(
        open=99.0, high=101.0, low=98.0, close=close, volume=1000.0,
        ts_event=ts, instrument_id=instrument_id,
    )


# ---------------------------------------------------------------------------
# Mock feature engine (Nautilus-free)
# ---------------------------------------------------------------------------

class MockFeatureEngine(FeatureEngineBase):
    """Minimal engine for testing; outputs simple counter features."""

    def __init__(self, multiplier: float = 1.0) -> None:
        self._multiplier = multiplier
        self._count = 0
        self._schema = _make_schema()

    @property
    def name(self) -> str:
        return "test_features_v1"

    @property
    def schema(self) -> FeatureSetSpec:
        return self._schema

    def reset(self) -> None:
        self._count = 0

    def update(self, event) -> FeatureEvent | None:
        if not isinstance(event, BarInput):
            return None
        self._count += 1
        ts = event.ts_event if event.ts_event is not None else 0
        iid = event.instrument_id or ""
        return FeatureEvent(
            ts_event=ts,
            instrument_id=iid,
            feature_set_id="test_features_v1",
            feature_version="1",
            values={"x": float(self._count) * self._multiplier, "y": event.close},
            source_event_type="bar",
        )

    def state_dict(self) -> dict:
        return {"count": self._count, "multiplier": self._multiplier}

    def load_state_dict(self, state: dict) -> None:
        self._count = state["count"]
        self._multiplier = state["multiplier"]


# ===========================================================================
# 1–4: Core type tests
# ===========================================================================

def test_feature_event_construct_and_to_row():
    fe = _make_fe(ts=_TS0, x=3.14, y=2.71)
    row = fe.to_row()
    assert row["ts_event"] == _TS0
    assert row["instrument_id"] == _IID
    assert row["feature_set_id"] == _FSID
    assert row["is_warmup"] is False
    assert row["x"] == pytest.approx(3.14)
    assert row["y"] == pytest.approx(2.71)
    # Must not create a DataFrame — row is a plain dict
    assert isinstance(row, dict)


def test_feature_event_from_row_round_trip():
    fe = FeatureEvent(
        ts_event=_TS0,
        instrument_id=_IID,
        feature_set_id=_FSID,
        feature_version=_VER,
        values={"x": 9.9, "y": 8.8},
        is_warmup=True,
        metadata={"run": "test"},
    )
    row = fe.to_row()
    fe2 = FeatureEvent.from_row(row, ["x", "y"])
    assert fe2.ts_event == fe.ts_event
    assert fe2.instrument_id == fe.instrument_id
    assert fe2.feature_set_id == fe.feature_set_id
    assert fe2.is_warmup is True
    assert fe2.values["x"] == pytest.approx(9.9)
    assert fe2.metadata == {"run": "test"}


def test_feature_schema_save_load_json(tmp_path: Path):
    spec = _make_schema()
    path = spec.save(tmp_path / "schema.json")
    loaded = FeatureSetSpec.load(path)
    assert loaded.feature_set_id == spec.feature_set_id
    assert loaded.version == spec.version
    assert len(loaded.output_features) == 2
    assert loaded.output_features[0].name == "x"
    assert loaded.point_in_time_safe is True


def test_feature_schema_output_names():
    spec = _make_schema()
    names = spec.output_feature_names()
    assert names == ["x", "y"]


# ===========================================================================
# 5–7: Engine protocol tests
# ===========================================================================

def test_feature_engine_base_update_many_default():
    engine = MockFeatureEngine()
    bars = [_make_bar(ts=_TS0 + i * 60_000) for i in range(5)]
    events = engine.update_many(bars)
    assert len(events) == 5
    assert events[0].values["x"] == pytest.approx(1.0)
    assert events[4].values["x"] == pytest.approx(5.0)


def test_feature_engine_update_returns_none_for_unknown_type():
    engine = MockFeatureEngine()
    # Passing a plain dict (not a BarInput) should return None
    result = engine.update({"close": 100.0})
    assert result is None


def test_mock_engine_implements_protocol():
    from nautilus_ext.features.feature_engine import BaseFeatureEngine
    engine = MockFeatureEngine()
    assert isinstance(engine, BaseFeatureEngine)


# ===========================================================================
# 8–10: Registry tests
# ===========================================================================

def test_feature_registry_register_and_build():
    @register_feature_engine("_test_reg_v99")
    class _TestEngine(MockFeatureEngine):
        @property
        def name(self) -> str:
            return "_test_reg_v99"

    engine = build_feature_engine("_test_reg_v99")
    assert engine.name == "_test_reg_v99"


def test_feature_registry_build_from_dict():
    # Register a parameterised engine
    @register_feature_engine("_test_multiplier_v1")
    class _MultEngine(MockFeatureEngine):
        @property
        def name(self) -> str:
            return "_test_multiplier_v1"

    engine = build_feature_engine(
        {"name": "_test_multiplier_v1", "params": {"multiplier": 2.5}}
    )
    bar = _make_bar(close=10.0)
    fe = engine.update(bar)
    assert fe.values["x"] == pytest.approx(2.5)


def test_feature_registry_available_includes_registered():
    @register_feature_engine("_test_avail_v1")
    class _AvailEngine(MockFeatureEngine):
        pass

    names = available_feature_engines()
    assert "_test_avail_v1" in names


# ===========================================================================
# 11–14: OnlineFeatureStore tests
# ===========================================================================

def test_online_store_put_get_latest():
    store = OnlineFeatureStore()
    fe = _make_fe(ts=_TS0)
    store.put(fe)
    latest = store.get_latest(_IID, _FSID)
    assert latest is fe


def test_online_store_get_window_n():
    store = OnlineFeatureStore()
    for i in range(5):
        store.put(_make_fe(ts=_TS0 + i * 1000, x=float(i)))
    window = store.get_window(_IID, _FSID, n=3)
    assert len(window) == 3
    assert window[-1].values["x"] == pytest.approx(4.0)


def test_online_store_get_window_time_filter():
    store = OnlineFeatureStore()
    for i in range(5):
        store.put(_make_fe(ts=_TS0 + i * 1000))
    window = store.get_window(_IID, _FSID, start=_TS0 + 1000, end=_TS0 + 3000)
    assert len(window) == 3


def test_online_store_clear_specific_key():
    store = OnlineFeatureStore()
    store.put(_make_fe(ts=_TS0))
    store.put(_make_fe(ts=_TS0, feature_set_id="other_set_v1", x=9.0))
    store.clear(instrument_id=_IID, feature_set_id=_FSID)
    assert store.get_latest(_IID, _FSID) is None
    # Other key still intact
    assert store.get_latest(_IID, "other_set_v1") is not None


# ===========================================================================
# 15–20: OfflineFeatureStore tests
# ===========================================================================

def test_offline_store_buffer_and_flush_writes_parquet(tmp_path: Path):
    store = OfflineFeatureStore(tmp_path)
    for i in range(10):
        store.append(_make_fe(ts=_TS0 + i * 1000))
    n = store.flush()
    assert n == 10
    # Check parquet file exists
    parquets = list(tmp_path.rglob("*.parquet"))
    assert len(parquets) >= 1
    df = pd.read_parquet(parquets[0])
    assert len(df) == 10
    assert "x" in df.columns


def test_offline_store_no_per_row_file_for_many_events(tmp_path: Path):
    store = OfflineFeatureStore(tmp_path, flush_threshold=10_000)
    for i in range(100):
        store.append(_make_fe(ts=_TS0 + i * 1000))
    assert store.pending_count() == 100
    n = store.flush()
    assert n == 100
    # All 100 rows in ONE file (single instrument, single feature set)
    parquets = list(tmp_path.rglob("*.parquet"))
    assert len(parquets) == 1


def test_offline_store_query_by_instrument_and_set(tmp_path: Path):
    store = OfflineFeatureStore(tmp_path)
    for i in range(5):
        store.append(_make_fe(ts=_TS0 + i * 1000, instrument_id="AAA_BINANCE"))
    for i in range(3):
        store.append(_make_fe(ts=_TS0 + i * 1000, instrument_id="BBB_BINANCE"))
    store.flush()

    df_a = store.query(instrument_id="AAA_BINANCE", feature_set_id=_FSID)
    df_b = store.query(instrument_id="BBB_BINANCE", feature_set_id=_FSID)
    assert len(df_a) == 5
    assert len(df_b) == 3


def test_offline_store_query_time_range(tmp_path: Path):
    store = OfflineFeatureStore(tmp_path)
    for i in range(10):
        store.append(_make_fe(ts=_TS0 + i * 60_000))
    store.flush()

    df = store.query(
        instrument_id=_IID,
        feature_set_id=_FSID,
        start=_TS0 + 60_000,
        end=_TS0 + 3 * 60_000,
    )
    assert len(df) == 3


def test_offline_store_excludes_warmup_by_default(tmp_path: Path):
    store = OfflineFeatureStore(tmp_path)
    for i in range(5):
        store.append(_make_fe(ts=_TS0 + i * 1000, is_warmup=True))
    for i in range(5):
        store.append(_make_fe(ts=_TS0 + 10_000 + i * 1000, is_warmup=False))
    store.flush()

    df_no_warmup = store.query(instrument_id=_IID, feature_set_id=_FSID)
    df_with_warmup = store.query(
        instrument_id=_IID, feature_set_id=_FSID, include_warmup=True
    )
    assert len(df_no_warmup) == 5
    assert len(df_with_warmup) == 10


def test_offline_store_write_load_schema(tmp_path: Path):
    store = OfflineFeatureStore(tmp_path)
    spec = _make_schema()
    store.write_schema(spec)
    loaded = store.load_schema(_FSID)
    assert loaded.feature_set_id == _FSID
    assert len(loaded.output_features) == 2


# ===========================================================================
# 21–26: FeaturePipeline tests
# ===========================================================================

def test_feature_pipeline_update_no_dataframe():
    engine = MockFeatureEngine()
    pipeline = FeaturePipeline([engine])
    bar = _make_bar()

    # Verify no DataFrame is created on the hot path
    import pandas as _pd
    _original_df_init = _pd.DataFrame.__init__

    df_calls = []

    def _counting_init(self, *a, **kw):
        df_calls.append(1)
        _original_df_init(self, *a, **kw)

    _pd.DataFrame.__init__ = _counting_init
    try:
        result = pipeline.update(bar)
    finally:
        _pd.DataFrame.__init__ = _original_df_init

    assert len(df_calls) == 0, "DataFrame was created during pipeline.update() — hot path violation"
    assert len(result) == 1
    assert result[0].values["y"] == pytest.approx(bar.close)


def test_feature_pipeline_warmup_marks_is_warmup():
    engine = MockFeatureEngine()
    pipeline = FeaturePipeline([engine])
    warmup_bars = [_make_bar(ts=_TS0 + i * 1000) for i in range(3)]
    warmup_events = pipeline.warmup(warmup_bars)
    assert all(fe.is_warmup for fe in warmup_events)


def test_feature_pipeline_warmup_then_live_continuity():
    engine = MockFeatureEngine()
    pipeline = FeaturePipeline([engine])
    warmup_bars = [_make_bar(ts=_TS0 + i * 1000) for i in range(10)]
    pipeline.warmup(warmup_bars)
    # Engine count should be 10 after warmup
    assert engine._count == 10

    live_bar = _make_bar(ts=_TS0 + 10_000)
    live_events = pipeline.update(live_bar)
    assert len(live_events) == 1
    assert live_events[0].is_warmup is False
    # Count is now 11
    assert engine._count == 11


def test_feature_pipeline_puts_to_online_store():
    engine = MockFeatureEngine()
    online = OnlineFeatureStore()
    pipeline = FeaturePipeline([engine], online_store=online)

    bar = _make_bar(ts=_TS0)
    pipeline.update(bar)

    latest = online.get_latest(_IID, "test_features_v1")
    assert latest is not None
    assert latest.ts_event == _TS0


def test_feature_pipeline_buffers_to_offline_store(tmp_path: Path):
    engine = MockFeatureEngine()
    offline = OfflineFeatureStore(tmp_path, flush_threshold=100_000)
    pipeline = FeaturePipeline([engine], offline_store=offline)

    for i in range(5):
        pipeline.update(_make_bar(ts=_TS0 + i * 1000))

    # Not yet flushed (threshold not reached)
    assert offline.pending_count() == 5

    pipeline.flush()
    assert offline.pending_count() == 0

    df = offline.query(instrument_id=_IID, feature_set_id="test_features_v1")
    assert len(df) == 5


def test_feature_pipeline_state_dict_round_trip():
    engine = MockFeatureEngine()
    pipeline = FeaturePipeline([engine])
    for i in range(3):
        pipeline.update(_make_bar(ts=_TS0 + i * 1000))

    state = pipeline.state_dict()
    assert "test_features_v1" in state

    # Restore into a fresh pipeline
    engine2 = MockFeatureEngine()
    pipeline2 = FeaturePipeline([engine2])
    pipeline2.load_state_dict(state)
    assert engine2._count == 3


# ===========================================================================
# 27–29: StrategyRuntimeContext tests
# ===========================================================================

def test_strategy_runtime_context_get_value():
    fe = _make_fe(x=42.0, y=7.0)
    ctx = StrategyRuntimeContext(
        event=_make_bar(),
        features={_FSID: fe},
        position=-1,
        bars_since_entry=3,
    )
    assert ctx.get_value(_FSID, "x") == pytest.approx(42.0)
    assert ctx.get_value(_FSID, "missing", default=0.0) == pytest.approx(0.0)
    assert ctx.get_feature_values(_FSID) == {"x": 42.0, "y": 7.0}


def test_strategy_runtime_context_get_compat():
    fe = _make_fe(x=5.0)
    ctx = StrategyRuntimeContext(
        event=_make_bar(),
        features={_FSID: fe},
        position=0,
        bars_since_entry=2,
    )
    # .get() is backward-compatible with dict-style access used by VWM engine
    assert ctx.get("position") == 0
    assert ctx.get("bars_since_entry") == 2
    assert ctx.get("nonexistent", default=99) == 99


def test_strategy_runtime_context_to_legacy_dict():
    fe = _make_fe(x=1.5, y=2.5)
    ctx = StrategyRuntimeContext(
        event=_make_bar(),
        features={_FSID: fe},
        position=-1,
        bars_since_entry=1,
    )
    d = ctx.to_legacy_dict()
    assert d["position"] == -1
    assert d["bars_since_entry"] == 1
    assert d[f"{_FSID}.x"] == pytest.approx(1.5)


# ===========================================================================
# 30–32: ML layer tests
# ===========================================================================

def test_feature_dataset_load_returns_dataframe(tmp_path: Path):
    # Write some feature events to an offline store
    store = OfflineFeatureStore(tmp_path / "features")
    for i in range(20):
        store.append(_make_fe(ts=_TS0 + i * 60_000, is_warmup=(i < 5)))
    store.flush()

    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID],
        instruments=[_IID],
    )
    df = load_feature_dataset(spec)
    # Warmup rows excluded by default
    assert len(df) == 15
    assert "x" in df.columns
    assert "y" in df.columns


def test_inference_context_feature_vector():
    store = OnlineFeatureStore()
    store.put(_make_fe(ts=_TS0, x=3.14, y=2.71))
    ctx = ModelInferenceContext(store, [_FSID])
    vec = ctx.get_feature_vector(_IID)
    assert vec[f"{_FSID}.x"] == pytest.approx(3.14)
    assert vec[f"{_FSID}.y"] == pytest.approx(2.71)


def test_inference_context_is_ready():
    store = OnlineFeatureStore()
    ctx = ModelInferenceContext(store, [_FSID])
    assert ctx.is_ready(_IID) is False
    store.put(_make_fe())
    assert ctx.is_ready(_IID) is True


# ===========================================================================
# 33–37: Integration tests
# ===========================================================================

def test_pipeline_runner_integration_with_mock_feed():
    """FeaturePipeline integrates with mock runner without real network."""
    engine = MockFeatureEngine()
    online = OnlineFeatureStore()
    pipeline = FeaturePipeline([engine], online_store=online)

    # Simulate warmup: 5 bars
    warmup_bars = [_make_bar(ts=_TS0 + i * 60_000) for i in range(5)]
    pipeline.warmup(warmup_bars)

    # Live: 3 bars
    live_bars = [_make_bar(ts=_TS0 + (5 + i) * 60_000) for i in range(3)]
    for bar in live_bars:
        pipeline.update(bar)

    latest = online.get_latest(_IID, "test_features_v1")
    assert latest is not None
    assert latest.is_warmup is False
    assert latest.values["x"] == pytest.approx(8.0)   # 5 warmup + 3 live


def test_old_vwm_signal_engine_unaffected():
    """Old VWM signal engine still works unchanged (Mode A, no context needed)."""
    try:
        from nautilus_ext.strategies.vwm_short_signals import (
            VolumeWeightedMomentumShortSignalEngine,
        )
        from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
    except ImportError:
        pytest.skip("Nautilus indicators not compiled")

    engine = VolumeWeightedMomentumShortSignalEngine(VwmShortSignalConfig())
    for i in range(30):
        bar = BarInput(open=99.0, high=101.0, low=98.0, close=100.0 + i * 0.1, volume=1000.0)
        result = engine.update(bar, position=0, bars_since_entry=0)
    assert result is not None
    assert result.debug is not None


def test_feature_joiner_join_df():
    bars_df = pd.DataFrame({
        "ts_event": [_TS0, _TS0 + 1000],
        "close": [100.0, 101.0],
    })
    features_df = pd.DataFrame({
        "ts_event": [_TS0, _TS0 + 1000],
        "x": [1.0, 2.0],
        "y": [3.0, 4.0],
        "instrument_id": [_IID, _IID],
    })
    joined = FeatureJoiner.join_df(bars_df, features_df)
    assert "x" in joined.columns
    assert len(joined) == 2
    assert joined.iloc[0]["x"] == pytest.approx(1.0)


def test_feature_query_cache_lru():
    cache = FeatureQueryCache(max_entries=2)
    df1 = pd.DataFrame({"x": [1, 2]})
    df2 = pd.DataFrame({"x": [3, 4]})
    df3 = pd.DataFrame({"x": [5, 6]})

    cache.put(df1, key="a")
    cache.put(df2, key="b")
    assert cache.get(key="a") is not None   # LRU: a → b
    cache.put(df3, key="c")                 # Should evict b (LRU is now a)
    assert cache.get(key="b") is None       # b was evicted
    assert cache.get(key="a") is not None
    assert cache.get(key="c") is not None
    assert len(cache) == 2


def test_feature_checkpoint_save_load(tmp_path: Path):
    engine = MockFeatureEngine()
    pipeline = FeaturePipeline([engine])
    for i in range(5):
        pipeline.update(_make_bar(ts=_TS0 + i * 1000))

    mgr = FeatureCheckpointManager(tmp_path / "checkpoints")
    mgr.save(pipeline, run_id="test_run_001")
    assert mgr.exists("test_run_001")
    assert "test_run_001" in mgr.list_checkpoints()

    # Restore into a fresh pipeline
    engine2 = MockFeatureEngine()
    pipeline2 = FeaturePipeline([engine2])
    mgr.load(pipeline2, run_id="test_run_001")
    assert engine2._count == 5


# ===========================================================================
# 38–40: VWM adapter tests (require Nautilus Cython)
# ===========================================================================

def _try_import_vwm_adapter():
    try:
        from nautilus_ext.features.vwm_adapter import VwmBarFeatureEngine
        return VwmBarFeatureEngine
    except (ImportError, ModuleNotFoundError):
        return None


@pytest.mark.nautilus_required
def test_vwm_bar_feature_engine_outputs_feature_event():
    VwmBarFeatureEngine = _try_import_vwm_adapter()
    if VwmBarFeatureEngine is None:
        pytest.skip("Nautilus indicators not compiled")
    engine = VwmBarFeatureEngine()
    for i in range(25):
        fe = engine.update(BarInput(
            open=99.0, high=101.0, low=98.0,
            close=100.0 + i * 0.1, volume=1000.0,
            ts_event=_TS0 + i * 60_000,
            instrument_id=_IID,
        ))
    assert isinstance(fe, FeatureEvent)
    assert fe.feature_set_id == "vwm_features_v1"
    assert "momentum" in fe.values
    assert "vwm" in fe.values
    assert "atr" in fe.values
    assert "bull_setup" in fe.values
    assert fe.instrument_id == _IID
    assert fe.ts_event == _TS0 + 24 * 60_000


@pytest.mark.nautilus_required
def test_vwm_bar_feature_engine_non_bar_returns_none():
    VwmBarFeatureEngine = _try_import_vwm_adapter()
    if VwmBarFeatureEngine is None:
        pytest.skip("Nautilus indicators not compiled")
    engine = VwmBarFeatureEngine()
    result = engine.update({"close": 100.0})
    assert result is None


@pytest.mark.nautilus_required
def test_vwm_bar_feature_engine_state_dict_round_trip():
    VwmBarFeatureEngine = _try_import_vwm_adapter()
    if VwmBarFeatureEngine is None:
        pytest.skip("Nautilus indicators not compiled")

    engine = VwmBarFeatureEngine()
    for i in range(30):
        engine.update(BarInput(
            open=99.0, high=101.0, low=98.0,
            close=100.0 + i * 0.1, volume=1000.0,
            ts_event=_TS0 + i * 60_000, instrument_id=_IID,
        ))
    state = engine.state_dict()

    engine2 = VwmBarFeatureEngine()
    engine2.load_state_dict(state)
    fe_orig = engine.update(BarInput(
        open=99.0, high=101.0, low=98.0,
        close=103.1, volume=1000.0,
        ts_event=_TS0 + 30 * 60_000, instrument_id=_IID,
    ))
    fe_rest = engine2.update(BarInput(
        open=99.0, high=101.0, low=98.0,
        close=103.1, volume=1000.0,
        ts_event=_TS0 + 30 * 60_000, instrument_id=_IID,
    ))
    assert fe_orig.values["vwm"] == pytest.approx(fe_rest.values["vwm"], rel=1e-6)


# ===========================================================================
# 41–46: New tests — BacktestRunner, Mode B, SignalRecorder feature_refs
# ===========================================================================

def test_backtest_runner_generates_offline_features(tmp_path: Path):
    """BacktestRunner with feature_pipeline writes offline parquet files."""
    # Build a mock data_connector returning BarInput-compatible mock bars.
    @dataclass
    class _MockBarType:
        instrument_id: str = "BTCUSDT-PERP.BINANCE"
        def __str__(self): return "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"

    @dataclass
    class _MockBar:
        open: float = 100.0
        high: float = 101.0
        low: float = 99.0
        close: float = 100.5
        volume: float = 1000.0
        ts_event: int = _TS0 * 1_000_000  # ns
        bar_type: object = None
        def __post_init__(self): self.bar_type = _MockBarType()

    class _MockConnector:
        @property
        def instrument(self): return None
        def prepare_data(self): return [_MockBar(ts_event=(_TS0 + i * 60_000) * 1_000_000) for i in range(10)]
        def get_bar_type(self): return _MockBarType()

    engine = MockFeatureEngine()
    online = OnlineFeatureStore()
    offline = OfflineFeatureStore(tmp_path / "features")
    pipeline = FeaturePipeline([engine], online_store=online, offline_store=offline)

    class _MockEngineConfig:
        pass

    from nautilus_ext.runners.backtest_runner import NautilusBacktestRunner

    # Use internal helper directly (avoids NautilusStrategySpec complexity)
    runner = NautilusBacktestRunner(_MockConnector(), _MockEngineConfig(), output_dir=str(tmp_path))
    bars = _MockConnector().prepare_data()
    n = runner._run_feature_pipeline(bars, pipeline, tmp_path / "features")

    assert n == 10
    parquets = list((tmp_path / "features").rglob("*.parquet"))
    assert len(parquets) >= 1
    df = pd.read_parquet(parquets[0])
    assert len(df) == 10
    assert "x" in df.columns


@pytest.mark.nautilus_required
def test_vwm_signal_engine_mode_b_reads_context():
    """VwmShortSignalEngine uses external FeatureEvent values when context provides them."""
    try:
        from nautilus_ext.strategies.vwm_short_signals import (
            VolumeWeightedMomentumShortSignalEngine,
        )
        from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
    except ImportError:
        pytest.skip("Nautilus indicators not compiled")

    # Warm up Mode A engine to a stable state
    engine = VolumeWeightedMomentumShortSignalEngine(VwmShortSignalConfig())
    for i in range(30):
        bar = BarInput(open=99.0, high=101.0, low=98.0, close=100.0 + i * 0.1, volume=1000.0,
                       ts_event=_TS0 + i * 60_000, instrument_id=_IID)
        engine.update(bar, position=0, bars_since_entry=0)

    # Build a FeatureEvent with controlled values
    controlled_fe = FeatureEvent(
        ts_event=_TS0 + 30 * 60_000,
        instrument_id=_IID,
        feature_set_id="vwm_features_v1",
        feature_version="1",
        values={
            "current_bar": 99,
            "momentum": 5.0,
            "vwm": 2.5,
            "atr": 1.0,
            "prev_vwm": 2.0,
            "prev_atr": 1.0,
            "bull_setup": False,
            "bear_setup": True,   # force bear_setup=True via external feature
        },
    )
    ctx = StrategyRuntimeContext(
        event=bar,
        features={"vwm_features_v1": controlled_fe},
        position=0,
        bars_since_entry=0,
    )

    bar31 = BarInput(open=99.0, high=101.0, low=98.0, close=103.1, volume=1000.0,
                     ts_event=_TS0 + 30 * 60_000, instrument_id=_IID)
    result = engine.update(bar31, context=ctx, position=0, bars_since_entry=0)

    # bear_setup=True from external features → se_price gets updated
    assert result is not None
    assert result.debug is not None
    assert result.debug["bear_setup"] is True


@pytest.mark.nautilus_required
def test_vwm_signal_engine_mode_a_fallback_when_no_context():
    """Without context, VwmShortSignalEngine still uses internal engine (Mode A)."""
    try:
        from nautilus_ext.strategies.vwm_short_signals import (
            VolumeWeightedMomentumShortSignalEngine,
        )
        from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
    except ImportError:
        pytest.skip("Nautilus indicators not compiled")

    engine = VolumeWeightedMomentumShortSignalEngine(VwmShortSignalConfig())
    bar = BarInput(open=99.0, high=101.0, low=98.0, close=100.0, volume=1000.0)
    result = engine.update(bar, position=0, bars_since_entry=0)
    assert result is not None
    # Internal features were updated (Mode A ran)
    assert engine.features.current_bar == 1


def test_signal_recorder_feature_refs_columns():
    """SignalRecorder stores feature_refs cross-reference columns."""
    from nautilus_ext.ccxt_live.signal_recorder import SignalRecorder

    recorder = SignalRecorder("BTCUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")

    row = pd.Series({
        "timestamp_ms": _TS0,
        "datetime": "2024-01-01T00:00:00",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0,
    })

    @dataclass
    class _FakeResult:
        entry_side: object = None
        exit_side: object = None
        reason: str | None = None
        signal_name: str | None = None
        debug: dict | None = None
        state: dict | None = None
        order_intents: list | None = None

    result = _FakeResult(debug={"current_bar": 5})
    feature_refs = {"feature_set_ids": "vwm_features_v1", "feature_event_ts": _TS0}

    recorder.append(row, result, 0, feature_refs=feature_refs)
    df = recorder.to_dataframe()
    assert "feature_set_ids" in df.columns
    assert "feature_event_ts" in df.columns
    assert df.iloc[0]["feature_set_ids"] == "vwm_features_v1"
    assert df.iloc[0]["feature_event_ts"] == _TS0


def test_signal_recorder_feature_refs_none_when_no_pipeline():
    """When no feature_pipeline, feature_refs columns are None (not error)."""
    from nautilus_ext.ccxt_live.signal_recorder import SignalRecorder

    recorder = SignalRecorder("TEST.BINANCE", "TEST.BINANCE-1-MINUTE-LAST-EXTERNAL")
    row = pd.Series({
        "timestamp_ms": _TS0, "datetime": "2024-01-01T00:00:00",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0,
    })

    @dataclass
    class _FakeResult:
        entry_side: object = None
        exit_side: object = None
        reason: str | None = None
        signal_name: str | None = None
        debug: dict | None = None
        state: dict | None = None
        order_intents: list | None = None

    recorder.append(row, _FakeResult(), 0)  # no feature_refs kwarg
    df = recorder.to_dataframe()
    assert df.iloc[0]["feature_set_ids"] is None
    assert df.iloc[0]["feature_event_ts"] is None


def test_pipeline_warmup_n_then_live_1_current_bar_is_n_plus_1():
    """After warmup(N) then update(1 live bar), engine count equals N+1."""
    N = 7
    engine = MockFeatureEngine()
    pipeline = FeaturePipeline([engine])
    warmup_bars = [_make_bar(ts=_TS0 + i * 60_000) for i in range(N)]
    pipeline.warmup(warmup_bars)
    assert engine._count == N

    live_bar = _make_bar(ts=_TS0 + N * 60_000)
    events = pipeline.update(live_bar)
    assert engine._count == N + 1
    assert len(events) == 1
    assert events[0].is_warmup is False
    assert events[0].values["x"] == pytest.approx(float(N + 1))


# ===========================================================================
# 47–55: Data layer / Feature Database optimisation tests
# ===========================================================================

def test_online_store_get_all_latest():
    """get_all_latest returns all feature_set_ids for one instrument — O(1) dict."""
    store = OnlineFeatureStore()
    fe1 = _make_fe(ts=_TS0, feature_set_id="fs_a", x=1.0)
    fe2 = _make_fe(ts=_TS0, feature_set_id="fs_b", x=2.0)
    store.put(fe1)
    store.put(fe2)

    latest = store.get_all_latest(_IID)
    assert set(latest.keys()) == {"fs_a", "fs_b"}
    assert latest["fs_a"].values["x"] == pytest.approx(1.0)
    assert latest["fs_b"].values["x"] == pytest.approx(2.0)

    # Returns empty dict for unknown instrument
    assert store.get_all_latest("UNKNOWN.VENUE") == {}


def test_online_store_latest_dict_reflects_most_recent_put():
    """After two puts for same key, get_latest always returns the last one."""
    store = OnlineFeatureStore()
    fe_old = _make_fe(ts=_TS0, x=1.0)
    fe_new = _make_fe(ts=_TS0 + 1000, x=9.9)
    store.put(fe_old)
    store.put(fe_new)

    # get_latest uses _latest dict — must be the second event
    result = store.get_latest(_IID, _FSID)
    assert result is fe_new
    assert result.values["x"] == pytest.approx(9.9)


def test_feature_manifest_basic_operations(tmp_path: Path):
    """Manifest supports append, save, load, find_files round-trip."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    path = tmp_path / "manifest.json"
    m = FeatureManifest(path)
    m.load()  # no file yet — should not raise
    assert len(m) == 0

    m.append_file_record(ManifestRecord(
        feature_set_id="vwm_features_v1",
        feature_version="1",
        instrument_id="BTCUSDT-PERP.BINANCE",
        start_ts=_TS0,
        end_ts=_TS0 + 9 * 60_000,
        row_count=10,
        file_path=str(tmp_path / "dummy.parquet"),
        created_at="2024-01-01T00:00:00+00:00",
    ))
    m.save()

    m2 = FeatureManifest(path)
    m2.load()
    assert len(m2) == 1
    assert m2.all_records()[0].feature_set_id == "vwm_features_v1"
    assert m2.all_records()[0].instrument_id == "BTCUSDT-PERP.BINANCE"


def test_feature_manifest_time_range_overlap_filter(tmp_path: Path):
    """find_files returns only files whose ts range overlaps the query range."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    m = FeatureManifest(tmp_path / "manifest.json")

    def _rec(start, end, path):
        return ManifestRecord(
            feature_set_id=_FSID, feature_version="1",
            instrument_id=_IID,
            start_ts=start, end_ts=end,
            row_count=1, file_path=path, created_at="",
        )

    # Files covering: [0-10], [5-15], [20-30]
    m.append_file_record(_rec(0, 10, "f1.parquet"))
    m.append_file_record(_rec(5, 15, "f2.parquet"))
    m.append_file_record(_rec(20, 30, "f3.parquet"))

    # Query [8, 12] overlaps f1 and f2 only
    files = m.find_files(start=8, end=12)
    assert set(files) == {"f1.parquet", "f2.parquet"}

    # Query [0, 30] overlaps all
    assert set(m.find_files(start=0, end=30)) == {"f1.parquet", "f2.parquet", "f3.parquet"}

    # Query [25, 30] overlaps only f3
    assert m.find_files(start=25, end=30) == ["f3.parquet"]

    # feature_set_id filter still works alongside time filter
    assert m.find_files(feature_set_id="other_fs", start=0, end=30) == []


def test_offline_store_manifest_populated_after_flush(tmp_path: Path):
    """After flush(), the manifest JSON records one entry per (iid, fsid) group."""
    store = OfflineFeatureStore(tmp_path)
    for i in range(5):
        store.append(_make_fe(ts=_TS0 + i * 1000))
    store.flush()

    manifest_path = tmp_path / "feature_manifest.json"
    assert manifest_path.exists()

    import json as _json
    records = _json.loads(manifest_path.read_text())
    assert len(records) == 1  # one group: (_IID, _FSID)
    assert records[0]["instrument_id"] == _IID
    assert records[0]["feature_set_id"] == _FSID
    assert records[0]["row_count"] == 5
    assert records[0]["start_ts"] == _TS0
    assert records[0]["end_ts"] == _TS0 + 4 * 1000


def test_offline_store_query_uses_manifest_not_rglob(tmp_path: Path):
    """query() reads files via manifest; rglob is not called when manifest is non-empty."""
    store = OfflineFeatureStore(tmp_path)
    for i in range(5):
        store.append(_make_fe(ts=_TS0 + i * 1000))
    store.flush()

    # Verify manifest is non-empty (query will use it)
    assert store._manifest is not None
    assert len(store._manifest) > 0

    # Monkeypatch rglob to detect if it's called on the offline root
    offline_root = tmp_path / "offline"
    original_rglob = offline_root.__class__.rglob
    rglob_called = []

    from pathlib import Path as _Path

    def _patched_rglob(self, pattern):
        if self == offline_root:
            rglob_called.append(pattern)
        return original_rglob(self, pattern)

    _Path.rglob = _patched_rglob
    try:
        df = store.query(instrument_id=_IID, feature_set_id=_FSID)
    finally:
        _Path.rglob = original_rglob

    assert len(df) == 5
    assert len(rglob_called) == 0, "rglob was called despite non-empty manifest"


def test_offline_store_extend_alias(tmp_path: Path):
    """extend() is an alias for write() — buffers without flushing."""
    store = OfflineFeatureStore(tmp_path, flush_threshold=10_000)
    events = [_make_fe(ts=_TS0 + i * 1000) for i in range(7)]
    store.extend(events)
    assert store.pending_count() == 7
    # No files written yet
    assert not (tmp_path / "offline").exists()
    store.flush()
    assert store.pending_count() == 0


def test_feature_dataset_select_columns(tmp_path: Path):
    """load_feature_dataset returns only requested columns plus required metadata."""
    store = OfflineFeatureStore(tmp_path / "features")
    for i in range(10):
        store.append(_make_fe(ts=_TS0 + i * 60_000))
    store.flush()

    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID],
        instruments=[_IID],
        select_columns=["x"],  # only want "x", not "y"
    )
    df = load_feature_dataset(spec)
    assert "x" in df.columns
    assert "y" not in df.columns
    # Required metadata always present
    assert "ts_event" in df.columns
    assert "instrument_id" in df.columns
    assert "feature_set_id" in df.columns
    assert "is_warmup" in df.columns
    assert len(df) == 10


def test_feature_manifest_validate_files_exist(tmp_path: Path):
    """validate_files_exist reports missing Parquet files."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    m = FeatureManifest(tmp_path / "manifest.json")
    real_file = tmp_path / "real.parquet"
    real_file.write_bytes(b"")  # create placeholder
    fake_file = str(tmp_path / "ghost.parquet")

    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1",
        instrument_id=_IID, start_ts=_TS0, end_ts=_TS0,
        row_count=1, file_path=str(real_file), created_at="",
    ))
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1",
        instrument_id=_IID, start_ts=_TS0 + 1000, end_ts=_TS0 + 1000,
        row_count=1, file_path=fake_file, created_at="",
    ))

    missing = m.validate_files_exist()
    assert missing == [fake_file]


def test_pipeline_get_latest_features_uses_get_all_latest():
    """FeaturePipeline.get_latest_features delegates to get_all_latest (O(1))."""
    engine = MockFeatureEngine()
    online = OnlineFeatureStore()
    pipeline = FeaturePipeline([engine], online_store=online)

    for i in range(3):
        pipeline.update(_make_bar(ts=_TS0 + i * 1000))

    features = pipeline.get_latest_features(_IID)
    assert "test_features_v1" in features
    assert features["test_features_v1"].values["x"] == pytest.approx(3.0)

    # Unknown instrument returns empty dict, not an error
    assert pipeline.get_latest_features("UNKNOWN.X") == {}


def test_pipeline_get_feature_window():
    """FeaturePipeline.get_feature_window returns last N events."""
    engine = MockFeatureEngine()
    online = OnlineFeatureStore()
    pipeline = FeaturePipeline([engine], online_store=online)

    for i in range(10):
        pipeline.update(_make_bar(ts=_TS0 + i * 1000))

    window = pipeline.get_feature_window(_IID, "test_features_v1", n=3)
    assert len(window) == 3
    assert window[-1].values["x"] == pytest.approx(10.0)  # last event

    # No online store → empty list
    pipeline2 = FeaturePipeline([MockFeatureEngine()])
    assert pipeline2.get_feature_window(_IID, "test_features_v1", n=5) == []


# ===========================================================================
# 58–75: FeatureManifest maintenance, multi-feature_set join, InferenceContext
# ===========================================================================

# --- Manifest maintenance -------------------------------------------------

def test_manifest_deduplicate_removes_exact_duplicates(tmp_path: Path):
    """deduplicate() removes records with identical (fs, ver, iid, start, end, path)."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    m = FeatureManifest(tmp_path / "manifest.json")

    def _rec(i, path="f.parquet"):
        return ManifestRecord(
            feature_set_id=_FSID, feature_version="1",
            instrument_id=_IID,
            start_ts=_TS0 + i, end_ts=_TS0 + i + 999,
            row_count=10, file_path=path, created_at="2024-01-01T00:00:00+00:00",
        )

    m.append_file_record(_rec(0, "a.parquet"))
    m.append_file_record(_rec(0, "a.parquet"))  # exact duplicate
    m.append_file_record(_rec(1, "b.parquet"))
    assert len(m) == 3

    removed = m.deduplicate()
    assert removed == 1
    assert len(m) == 2


def test_manifest_deduplicate_keeps_last_occurrence():
    """When file_path differs but other fields are equal, both records survive."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    m = FeatureManifest("/tmp/never_saved.json")
    for path in ("a.parquet", "b.parquet"):
        m.append_file_record(ManifestRecord(
            feature_set_id=_FSID, feature_version="1",
            instrument_id=_IID, start_ts=0, end_ts=999,
            row_count=5, file_path=path, created_at="",
        ))
    removed = m.deduplicate()
    # Different file_paths → different keys → both kept
    assert removed == 0
    assert len(m) == 2


def test_manifest_compact_keeps_one_per_time_slot(tmp_path: Path):
    """compact() keeps one record per (fs, ver, iid, start, end) group."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    m = FeatureManifest(tmp_path / "manifest.json")
    # Two records for the same time slot with different file_paths and created_at
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
        start_ts=0, end_ts=999, row_count=5,
        file_path="old.parquet", created_at="2024-01-01T00:00:00+00:00",
    ))
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
        start_ts=0, end_ts=999, row_count=5,
        file_path="new.parquet", created_at="2024-06-01T00:00:00+00:00",
    ))
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
        start_ts=1000, end_ts=1999, row_count=5,
        file_path="other.parquet", created_at="2024-01-01T00:00:00+00:00",
    ))
    assert len(m) == 3

    removed = m.compact(keep="latest")
    assert removed == 1   # one duplicate slot removed
    assert len(m) == 2
    # The kept record for slot [0,999] is the latest one
    for r in m.all_records():
        if r.start_ts == 0:
            assert r.file_path == "new.parquet"


def test_manifest_remove_missing_files(tmp_path: Path):
    """remove_missing_files() deletes records for files that don't exist on disk."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    real = tmp_path / "real.parquet"
    real.write_bytes(b"")

    m = FeatureManifest(tmp_path / "manifest.json")
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
        start_ts=_TS0, end_ts=_TS0 + 999, row_count=5,
        file_path=str(real), created_at="",
    ))
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
        start_ts=_TS0 + 1000, end_ts=_TS0 + 1999, row_count=5,
        file_path=str(tmp_path / "ghost.parquet"), created_at="",
    ))
    assert len(m) == 2

    removed = m.remove_missing_files()
    assert removed == [str(tmp_path / "ghost.parquet")]
    assert len(m) == 1
    assert m.all_records()[0].file_path == str(real)


def test_manifest_remove_missing_does_not_delete_real_files(tmp_path: Path):
    """remove_missing_files() never deletes files from disk, only manifest records."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    real = tmp_path / "keep.parquet"
    real.write_bytes(b"")

    m = FeatureManifest(tmp_path / "manifest.json")
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
        start_ts=_TS0, end_ts=_TS0 + 999, row_count=5,
        file_path=str(real), created_at="",
    ))
    m.remove_missing_files()
    assert real.exists()


def test_manifest_summary_returns_correct_stats(tmp_path: Path):
    """summary() returns per-(feature_set_id, instrument_id) aggregated stats."""
    from nautilus_ext.features.feature_manifest import FeatureManifest, ManifestRecord

    m = FeatureManifest(tmp_path / "manifest.json")
    iid_b = "ETHUSDT-PERP.BINANCE"

    for i in range(3):
        m.append_file_record(ManifestRecord(
            feature_set_id=_FSID, feature_version="1", instrument_id=_IID,
            start_ts=_TS0 + i * 1000, end_ts=_TS0 + i * 1000 + 999,
            row_count=10, file_path=f"btc_{i}.parquet", created_at="",
        ))
    m.append_file_record(ManifestRecord(
        feature_set_id=_FSID, feature_version="1", instrument_id=iid_b,
        start_ts=_TS0, end_ts=_TS0 + 4999,
        row_count=50, file_path="eth_0.parquet", created_at="",
    ))

    stats = m.summary()
    assert _FSID in stats
    btc_stats = stats[_FSID][_IID]
    assert btc_stats["file_count"] == 3
    assert btc_stats["total_row_count"] == 30
    assert btc_stats["min_start_ts"] == _TS0
    assert btc_stats["max_end_ts"] == _TS0 + 2999

    eth_stats = stats[_FSID][iid_b]
    assert eth_stats["file_count"] == 1
    assert eth_stats["total_row_count"] == 50


# --- Multi-feature-set FeatureDataset join --------------------------------

_FSID_B = "test_features_v2"

def _make_store_with_two_sets(
    tmp_path: Path,
    n: int = 10,
) -> OfflineFeatureStore:
    """Write two feature sets to one store, same timestamps."""
    store = OfflineFeatureStore(tmp_path)
    for i in range(n):
        ts = _TS0 + i * 60_000
        store.append(FeatureEvent(
            ts_event=ts, instrument_id=_IID,
            feature_set_id=_FSID, feature_version="1",
            values={"x": float(i), "y": float(i) * 2.0},
        ))
        store.append(FeatureEvent(
            ts_event=ts, instrument_id=_IID,
            feature_set_id=_FSID_B, feature_version="1",
            values={"z": float(i) * 3.0, "w": float(i) * 4.0},
        ))
    store.flush()
    return store


def test_feature_dataset_concat_mode_backward_compat(tmp_path: Path):
    """Default concat mode stacks rows vertically — backward-compatible."""
    _make_store_with_two_sets(tmp_path / "features")
    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID, _FSID_B],
        instruments=[_IID],
        join_mode="concat",
    )
    df = load_feature_dataset(spec)
    assert len(df) == 20   # 10 rows × 2 feature sets
    assert set(df["feature_set_id"].unique()) == {_FSID, _FSID_B}


def test_feature_dataset_exact_join_two_sets(tmp_path: Path):
    """Exact join produces one row per timestamp with columns from both sets."""
    _make_store_with_two_sets(tmp_path / "features")
    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID, _FSID_B],
        instruments=[_IID],
        join_mode="exact",
        column_prefix=True,
    )
    df = load_feature_dataset(spec)
    assert len(df) == 10   # inner join: same 10 timestamps
    # Columns from feature set A are prefixed
    assert f"{_FSID}__x" in df.columns
    assert f"{_FSID}__y" in df.columns
    # Columns from feature set B are prefixed
    assert f"{_FSID_B}__z" in df.columns
    assert f"{_FSID_B}__w" in df.columns
    # Shared metadata columns appear only once
    assert "ts_event" in df.columns
    assert "instrument_id" in df.columns


def test_feature_dataset_exact_join_no_column_conflicts(tmp_path: Path):
    """Exact join with column_prefix=True: identical column names from different sets do not collide."""
    store = OfflineFeatureStore(tmp_path / "features")
    for i in range(5):
        ts = _TS0 + i * 60_000
        # Both sets have a column named "x"
        store.append(FeatureEvent(
            ts_event=ts, instrument_id=_IID,
            feature_set_id="fs_a", feature_version="1",
            values={"x": float(i)},
        ))
        store.append(FeatureEvent(
            ts_event=ts, instrument_id=_IID,
            feature_set_id="fs_b", feature_version="1",
            values={"x": float(i) * 10},
        ))
    store.flush()

    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=["fs_a", "fs_b"],
        join_mode="exact",
        column_prefix=True,
    )
    df = load_feature_dataset(spec)
    assert "fs_a__x" in df.columns
    assert "fs_b__x" in df.columns
    # Values must be distinct (no overwrite)
    assert df["fs_b__x"].iloc[0] == pytest.approx(df["fs_a__x"].iloc[0] * 10)


def test_feature_dataset_asof_join_point_in_time(tmp_path: Path):
    """asof join never uses secondary features from after the primary ts_event."""
    store = OfflineFeatureStore(tmp_path / "features")
    # Primary (fs_a): bars at 0, 60, 120, 180 s
    # Secondary (fs_b): bars at 30, 90, 150 s (staggered by 30 s)
    for i in range(4):
        store.append(FeatureEvent(
            ts_event=_TS0 + i * 60_000, instrument_id=_IID,
            feature_set_id="fs_a", feature_version="1",
            values={"primary": float(i)},
        ))
    for i in range(3):
        store.append(FeatureEvent(
            ts_event=_TS0 + 30_000 + i * 60_000, instrument_id=_IID,
            feature_set_id="fs_b", feature_version="1",
            values={"secondary": float(i + 100)},
        ))
    store.flush()

    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=["fs_a", "fs_b"],
        join_mode="asof",
        column_prefix=True,
    )
    df = load_feature_dataset(spec)
    assert len(df) == 4  # all primary rows preserved

    # Primary at ts=0: no secondary at ts<=0 → NaN
    row0 = df[df["ts_event"] == _TS0].iloc[0]
    assert pd.isna(row0.get("fs_b__secondary", float("nan")))

    # Primary at ts=60: closest secondary at ts=30 (secondary=100) — direction=backward
    row60 = df[df["ts_event"] == _TS0 + 60_000].iloc[0]
    assert row60["fs_b__secondary"] == pytest.approx(100.0)

    # Primary at ts=120: closest secondary at ts=90 (secondary=101)
    row120 = df[df["ts_event"] == _TS0 + 120_000].iloc[0]
    assert row120["fs_b__secondary"] == pytest.approx(101.0)


def test_feature_dataset_select_columns_dict_per_set(tmp_path: Path):
    """select_columns as dict applies per-feature-set column filters."""
    _make_store_with_two_sets(tmp_path / "features")
    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID, _FSID_B],
        join_mode="exact",
        column_prefix=True,
        select_columns={_FSID: ["x"], _FSID_B: ["z"]},
    )
    df = load_feature_dataset(spec)
    assert f"{_FSID}__x" in df.columns
    assert f"{_FSID}__y" not in df.columns   # filtered out
    assert f"{_FSID_B}__z" in df.columns
    assert f"{_FSID_B}__w" not in df.columns  # filtered out


def test_load_feature_dataset_with_metadata_returns_correct_info(tmp_path: Path):
    """load_feature_dataset_with_metadata populates all result fields."""
    from nautilus_ext.ml.feature_dataset import load_feature_dataset_with_metadata

    store = OfflineFeatureStore(tmp_path / "features")
    for i in range(5):
        store.append(_make_fe(ts=_TS0 + i * 60_000))
    store.flush()

    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID],
        instruments=[_IID],
    )
    result = load_feature_dataset_with_metadata(spec)
    assert result.row_count == 5
    assert "ts_event" in result.columns
    assert result.start == _TS0
    assert result.end == _TS0 + 4 * 60_000
    assert _FSID in result.used_feature_sets


def test_load_feature_dataset_with_metadata_empty_store(tmp_path: Path):
    """Empty store returns FeatureDatasetResult with empty data."""
    from nautilus_ext.ml.feature_dataset import load_feature_dataset_with_metadata

    spec = FeatureDatasetSpec(
        feature_store_path=tmp_path / "features",
        feature_set_ids=[_FSID],
    )
    result = load_feature_dataset_with_metadata(spec)
    assert result.row_count == 0
    assert result.data.empty
    assert result.start is None
    assert result.end is None


# --- InferenceContext enhancements ----------------------------------------

def _make_inference_store() -> tuple[OnlineFeatureStore, str, str]:
    """Populate a store with two feature sets and return (store, iid, fsid)."""
    store = OnlineFeatureStore()
    store.put(FeatureEvent(
        ts_event=_TS0, instrument_id=_IID,
        feature_set_id="fs_a", feature_version="1",
        values={"m": 1.5, "v": 2.5},
    ))
    store.put(FeatureEvent(
        ts_event=_TS0, instrument_id=_IID,
        feature_set_id="fs_b", feature_version="1",
        values={"atr": 0.5},
    ))
    return store


def test_inference_context_multi_set_vector():
    """get_feature_vector assembles features from multiple feature sets."""
    store = _make_inference_store()
    ctx = ModelInferenceContext(store, feature_set_ids=["fs_a", "fs_b"])
    vec = ctx.get_feature_vector(_IID)
    assert vec["fs_a.m"] == pytest.approx(1.5)
    assert vec["fs_a.v"] == pytest.approx(2.5)
    assert vec["fs_b.atr"] == pytest.approx(0.5)


def test_inference_context_feature_order_controls_output():
    """feature_order determines the key order and filters the output."""
    store = _make_inference_store()
    ctx = ModelInferenceContext(
        store,
        feature_set_ids=["fs_a", "fs_b"],
        feature_order=["fs_b.atr", "fs_a.m"],
    )
    vec = ctx.get_feature_vector(_IID)
    keys = list(vec.keys())
    assert keys == ["fs_b.atr", "fs_a.m"]


def test_inference_context_fill_none_policy():
    """fill_none: missing keys map to None without raising."""
    store = OnlineFeatureStore()
    store.put(FeatureEvent(
        ts_event=_TS0, instrument_id=_IID,
        feature_set_id="fs_a", feature_version="1",
        values={"x": 1.0},
    ))
    ctx = ModelInferenceContext(
        store,
        feature_set_ids=["fs_a"],
        feature_order=["fs_a.x", "fs_a.missing"],
        missing_feature_policy="fill_none",
    )
    vec = ctx.get_feature_vector(_IID)
    assert vec["fs_a.x"] == pytest.approx(1.0)
    assert vec["fs_a.missing"] is None


def test_inference_context_fill_zero_policy():
    """fill_zero: missing keys map to 0.0."""
    store = OnlineFeatureStore()
    store.put(FeatureEvent(
        ts_event=_TS0, instrument_id=_IID,
        feature_set_id="fs_a", feature_version="1",
        values={"x": 1.0},
    ))
    ctx = ModelInferenceContext(
        store,
        feature_set_ids=["fs_a"],
        feature_order=["fs_a.x", "fs_a.missing"],
        missing_feature_policy="fill_zero",
    )
    vec = ctx.get_feature_vector(_IID)
    assert vec["fs_a.missing"] == pytest.approx(0.0)


def test_inference_context_raise_policy():
    """raise: ValueError raised when any feature in feature_order is missing."""
    store = OnlineFeatureStore()
    store.put(FeatureEvent(
        ts_event=_TS0, instrument_id=_IID,
        feature_set_id="fs_a", feature_version="1",
        values={"x": 1.0},
    ))
    ctx = ModelInferenceContext(
        store,
        feature_set_ids=["fs_a"],
        feature_order=["fs_a.x", "fs_a.missing"],
        missing_feature_policy="raise",
    )
    with pytest.raises(ValueError, match="missing"):
        ctx.get_feature_vector(_IID)


def test_inference_context_invalid_policy_raises():
    """Invalid missing_feature_policy raises ValueError at construction."""
    store = OnlineFeatureStore()
    with pytest.raises(ValueError):
        ModelInferenceContext(store, feature_set_ids=["fs_a"], missing_feature_policy="bad_value")


def test_inference_context_get_feature_list():
    """get_feature_list returns values in feature_order sequence."""
    store = _make_inference_store()
    ctx = ModelInferenceContext(
        store,
        feature_set_ids=["fs_a", "fs_b"],
        feature_order=["fs_b.atr", "fs_a.m", "fs_a.v"],
    )
    lst = ctx.get_feature_list(_IID)
    assert lst == pytest.approx([0.5, 1.5, 2.5])


def test_inference_context_get_feature_array_returns_iterable():
    """get_feature_array returns something list-like (numpy or list)."""
    store = _make_inference_store()
    ctx = ModelInferenceContext(
        store,
        feature_set_ids=["fs_a"],
        feature_order=["fs_a.m", "fs_a.v"],
    )
    arr = ctx.get_feature_array(_IID)
    values = list(arr)
    assert values == pytest.approx([1.5, 2.5])


# --- Benchmark import dry-run --------------------------------------------

def test_benchmark_script_importable():
    """benchmark_feature_store.py must be importable without side effects."""
    import importlib.util, sys
    from pathlib import Path as _Path
    spec_path = _Path(__file__).parents[2] / "scripts" / "benchmark_feature_store.py"
    if not spec_path.exists():
        pytest.skip("benchmark script not found")
    spec_mod = importlib.util.spec_from_file_location("benchmark_feature_store", spec_path)
    mod = importlib.util.module_from_spec(spec_mod)
    # Import must not raise and must not execute benchmarks
    spec_mod.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "bench_online_latency")


# ===========================================================================
# 76–83: E2E demo, templates, and strategy spec tests
# ===========================================================================

def test_feature_database_demo_importable():
    """run_feature_database_demo.py must be importable without side effects."""
    import importlib.util
    from pathlib import Path as _Path
    demo_path = (
        _Path(__file__).parents[2]
        / "examples"
        / "nautilus_ext_feature_database"
        / "run_feature_database_demo.py"
    )
    if not demo_path.exists():
        pytest.skip("demo script not found")
    spec_mod = importlib.util.spec_from_file_location("run_feature_database_demo", demo_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "run_demo")
    assert hasattr(mod, "DemoMomEngine")


def test_feature_database_demo_runs_in_tmp(tmp_path):
    """run_demo() completes without error and returns a valid summary dict."""
    import importlib.util
    from pathlib import Path as _Path
    demo_path = (
        _Path(__file__).parents[2]
        / "examples"
        / "nautilus_ext_feature_database"
        / "run_feature_database_demo.py"
    )
    if not demo_path.exists():
        pytest.skip("demo script not found")
    spec_mod = importlib.util.spec_from_file_location("run_feature_database_demo", demo_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)

    summary = mod.run_demo(tmp_path / "demo_out")

    assert summary["generated_events"] > 0
    assert summary["manifest_records"] > 0
    assert summary["dataset_shape"][0] > 0      # at least one training row
    assert summary["dataset_shape"][1] > 0      # at least one column
    assert len(summary["inference_vector_keys"]) > 0


def test_feature_database_demo_writes_parquet(tmp_path):
    """run_demo() writes at least one Parquet file under offline/."""
    import importlib.util
    from pathlib import Path as _Path
    demo_path = (
        _Path(__file__).parents[2]
        / "examples"
        / "nautilus_ext_feature_database"
        / "run_feature_database_demo.py"
    )
    if not demo_path.exists():
        pytest.skip("demo script not found")
    spec_mod = importlib.util.spec_from_file_location("run_feature_database_demo", demo_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)

    out_dir = tmp_path / "demo_parquet"
    mod.run_demo(out_dir)

    parquet_files = list(out_dir.rglob("*.parquet"))
    assert len(parquet_files) >= 1, "Expected at least one Parquet file after flush"

    manifest_path = out_dir / "feature_manifest.json"
    assert manifest_path.exists(), "feature_manifest.json must be written"


def test_feature_database_demo_dataset_readable(tmp_path):
    """FeatureDataset can load the Parquet files produced by run_demo()."""
    import importlib.util
    from pathlib import Path as _Path
    demo_path = (
        _Path(__file__).parents[2]
        / "examples"
        / "nautilus_ext_feature_database"
        / "run_feature_database_demo.py"
    )
    if not demo_path.exists():
        pytest.skip("demo script not found")
    spec_mod = importlib.util.spec_from_file_location("run_feature_database_demo", demo_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)

    out_dir = tmp_path / "demo_dataset"
    mod.run_demo(out_dir)

    from nautilus_ext.ml.feature_dataset import FeatureDatasetSpec, load_feature_dataset
    spec = FeatureDatasetSpec(
        feature_store_path=out_dir,
        feature_set_ids=["demo_mom_v1"],
        include_warmup=False,
    )
    df = load_feature_dataset(spec)
    assert not df.empty, "FeatureDataset should return non-empty DataFrame"
    assert "momentum" in df.columns
    # Warmup rows are excluded — all remaining rows have is_warmup=False
    assert (df["is_warmup"] == False).all()


def test_feature_database_demo_inference_context(tmp_path):
    """InferenceContext returns a non-empty feature vector after run_demo()."""
    import importlib.util
    from pathlib import Path as _Path
    demo_path = (
        _Path(__file__).parents[2]
        / "examples"
        / "nautilus_ext_feature_database"
        / "run_feature_database_demo.py"
    )
    if not demo_path.exists():
        pytest.skip("demo script not found")
    spec_mod = importlib.util.spec_from_file_location("run_feature_database_demo", demo_path)
    mod = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(mod)

    out_dir = tmp_path / "demo_infer"
    summary = mod.run_demo(out_dir)

    assert len(summary["inference_vector_keys"]) == 4  # close_ma, momentum, vol_sum, bar_count
    # All values should be numeric (not None) — engine ran enough bars
    for v in summary["inference_vector_values"]:
        assert v is not None


def test_feature_engine_template_importable():
    """example_feature_engine.py must be importable and register correctly."""
    from nautilus_ext.features.templates.example_feature_engine import (
        ExampleObvEngine,
        FEATURE_SET_ID,
        MY_FEATURE_SCHEMA,
    )
    assert FEATURE_SET_ID == "example_obv_v1"
    engine = ExampleObvEngine(window=10)
    assert engine.name == "example_obv_v1"
    assert engine.schema is MY_FEATURE_SCHEMA

    # state_dict round-trip
    state = engine.state_dict()
    engine2 = ExampleObvEngine(window=10)
    engine2.load_state_dict(state)
    assert engine2._window == 10


def test_feature_engine_template_update_emits_event():
    """ExampleObvEngine.update() returns FeatureEvent for BarInput, None otherwise."""
    from nautilus_ext.features.templates.example_feature_engine import ExampleObvEngine
    engine = ExampleObvEngine(window=5)

    bar = _make_bar(ts=_TS0, close=100.0)
    fe = engine.update(bar)
    assert fe is not None
    assert fe.feature_set_id == "example_obv_v1"
    assert fe.values["bar_count"] == 1

    # Returns None for non-BarInput
    fe2 = engine.update("not_a_bar")
    assert fe2 is None


def test_signal_engine_template_importable():
    """example_signal_engine.py must be importable and have correct interface."""
    from nautilus_ext.strategies.templates.example_signal_engine import (
        ExampleObvSignalEngine,
        SIGNAL_NAME,
        REQUIRES_FEATURES,
    )
    assert SIGNAL_NAME == "example_obv_signal_v1"
    assert "example_obv_v1" in REQUIRES_FEATURES

    engine = ExampleObvSignalEngine()
    assert engine.name == SIGNAL_NAME
    assert engine.requires_features == REQUIRES_FEATURES

    # state_dict round-trip
    state = engine.state_dict()
    engine2 = ExampleObvSignalEngine()
    engine2.load_state_dict(state)
    assert engine2._roc_threshold == engine._roc_threshold


def test_signal_engine_template_returns_hold_without_context():
    """ExampleObvSignalEngine returns a hold signal when context is None."""
    from nautilus_ext.strategies.templates.example_signal_engine import ExampleObvSignalEngine
    from nautilus_ext.strategies.interfaces.output_types import SignalResult
    engine = ExampleObvSignalEngine()
    bar = _make_bar(ts=_TS0)
    result = engine.update(bar, context=None)
    assert isinstance(result, SignalResult)
    assert result.order_intents == []
