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
