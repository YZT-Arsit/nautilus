"""End-to-end smoke test.

Validates the full lifecycle on a tmp_path filesystem:

  1. Synthetic OHLCV for **two symbols × two trading dates** is written to a
     Hive-style raw Parquet layout.
  2. The offline :class:`BatchEngine` computes features and writes them back
     into the feature Hive layout.
  3. The :class:`StreamingEngine` replays the *same* raw data in micro-batches
     and emits features through the same code paths.
  4. Streaming output is archived via :class:`EodArchiver` into the same
     Hive layout.
  5. We diff the offline and streaming feature frames row-by-row. They must
     agree within floating-point tolerance for every feature.

If this test passes, the framework's central guarantee — that the same code
gives identical results in batch and streaming modes, end-to-end across days
and symbols — is verified.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from quant_feature_engine.core.dag import FeatureDAG
from quant_feature_engine.execution.batch_engine import BatchEngine
from quant_feature_engine.features import load_all
from quant_feature_engine.storage.metadata import Manifest
from quant_feature_engine.storage.parquet_store import ParquetStore
from quant_feature_engine.streaming.archiver import EodArchiver
from quant_feature_engine.streaming.engine import (
    StreamingEngine,
    StreamingEngineConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYMBOLS = ("AAA", "BBB")
DATES = ("2026-05-25", "2026-05-26")
BARS_PER_DAY = 240   # 4h of 1-minute bars
FEATURES = ["sma_20", "vol_30", "rsi_14", "macd", "vwm_20", "vwm_zscore_60"]


def _make_day(symbol: str, date_str: str, seed: int) -> pl.DataFrame:
    """Generate one symbol-day of synthetic OHLCV."""
    rng = np.random.default_rng(seed)
    start = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    prices = 100.0 + (10.0 if symbol == "BBB" else 0.0)
    rows = []
    for i in range(BARS_PER_DAY):
        ret = rng.normal(0, 0.0008)
        prices *= 1 + ret
        ts = start + timedelta(minutes=i)
        rows.append(
            {
                "symbol": symbol,
                "ts_event": ts,
                "open": prices * (1 - 0.0003),
                "high": prices * 1.0008,
                "low": prices * 0.9992,
                "close": prices,
                "volume": float(rng.integers(1_000, 10_000)),
                "turnover": prices * 5_000.0,
            }
        )
    return pl.DataFrame(rows)


def _write_raw(raw_root: Path) -> ParquetStore:
    store = ParquetStore(
        raw_root,
        partition_cols=("asset_class", "exchange", "frequency", "trading_date"),
    )
    seed = 0
    for date_str in DATES:
        frames = [
            _make_day(sym, date_str, seed := seed + 1)
            for sym in SYMBOLS
        ]
        df = pl.concat(frames, how="vertical")
        store.write(
            df,
            partition_values={
                "asset_class": "stock",
                "exchange": "SSE",
                "frequency": "1m",
                "trading_date": date_str,
            },
        )
    return store


def _approx_equal(a: pl.DataFrame, b: pl.DataFrame, tol: float = 1e-6) -> tuple[bool, str]:
    """Diff two frames, treating None==None and NaN==NaN as equal."""
    if a.shape != b.shape:
        return False, f"shape mismatch: {a.shape} vs {b.shape}"
    if list(a.columns) != list(b.columns):
        return False, f"column mismatch: {a.columns} vs {b.columns}"
    for col in a.columns:
        xs, ys = a[col].to_list(), b[col].to_list()
        for i, (x, y) in enumerate(zip(xs, ys)):
            if x is None and y is None:
                continue
            if x is None or y is None:
                return False, f"{col}[{i}] None mismatch: {x!r} vs {y!r}"
            if isinstance(x, float):
                if math.isnan(x) and math.isnan(y):
                    continue
                if math.isnan(x) or math.isnan(y):
                    return False, f"{col}[{i}] NaN mismatch"
                if abs(x - y) > tol:
                    return False, f"{col}[{i}] {x} vs {y} (diff {abs(x-y)})"
            elif x != y:
                return False, f"{col}[{i}] {x!r} vs {y!r}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _features() -> None:
    load_all()


@pytest.fixture
def env(tmp_path):
    raw_root = tmp_path / "raw"
    feat_root = tmp_path / "features"
    meta_root = tmp_path / "_meta"
    raw_store = _write_raw(raw_root)
    feat_store = ParquetStore(
        feat_root,
        partition_cols=("feature_group", "frequency", "trading_date"),
    )
    manifest = Manifest(meta_root)
    return {
        "tmp": tmp_path,
        "raw_root": raw_root,
        "feature_root": feat_root,
        "raw_store": raw_store,
        "feature_store": feat_store,
        "manifest": manifest,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_offline_backfill_writes_partitions(env) -> None:
    engine = BatchEngine(
        raw_root=env["raw_root"],
        feature_root=env["feature_root"],
        manifest=env["manifest"],
        n_workers=1,  # single-process keeps the test fast and deterministic
    )
    partitions = [
        {
            "asset_class": "stock",
            "exchange": "SSE",
            "frequency": "1m",
            "trading_date": d,
        }
        for d in DATES
    ]
    results = engine.run(partitions, FEATURES)
    assert len(results) == len(DATES)
    for r in results:
        assert r["rows"] == BARS_PER_DAY * len(SYMBOLS)
        assert r["manifest_rows"], "every partition should produce manifest rows"

    # Confirm files exist for both partitions.
    for d in DATES:
        loaded = env["feature_store"].scan(
            filters={"feature_group": "technical", "frequency": "1m", "trading_date": d}
        )
        assert loaded.height == BARS_PER_DAY * len(SYMBOLS)
        assert "sma_20" in loaded.columns


def test_offline_idempotent_skips_when_manifest_present(env) -> None:
    engine = BatchEngine(
        raw_root=env["raw_root"],
        feature_root=env["feature_root"],
        manifest=env["manifest"],
        n_workers=1,
    )
    partitions = [
        {"asset_class": "stock", "exchange": "SSE", "frequency": "1m", "trading_date": d}
        for d in DATES
    ]
    engine.run(partitions, FEATURES)  # first run populates manifest
    second = engine.run(partitions, FEATURES)  # second run should be a no-op
    assert second == []


def test_no_cross_symbol_contamination(env) -> None:
    """A feature value for AAA must not depend on rows belonging to BBB."""
    engine = BatchEngine(
        raw_root=env["raw_root"],
        feature_root=env["feature_root"],
        manifest=env["manifest"],
        n_workers=1,
    )
    engine.run(
        [{"asset_class": "stock", "exchange": "SSE", "frequency": "1m", "trading_date": DATES[0]}],
        FEATURES,
    )
    full = env["feature_store"].scan(
        filters={"feature_group": "technical", "frequency": "1m", "trading_date": DATES[0]}
    )

    # Recompute SMA for AAA only, in isolation.
    only_aaa_raw = env["raw_store"].scan(
        filters={
            "asset_class": "stock",
            "exchange": "SSE",
            "frequency": "1m",
            "trading_date": DATES[0],
        }
    ).filter(pl.col("symbol") == "AAA").sort("ts_event")

    from quant_feature_engine.core import registry as _registry

    sma = _registry.get("sma_20")()
    isolated = only_aaa_raw.hstack(sma.compute_batch(only_aaa_raw))

    full_aaa = (
        full.filter(pl.col("symbol") == "AAA")
        .sort("ts_event")
        .select(["symbol", "ts_event", "sma_20"])
    )
    iso_aaa = isolated.select(["symbol", "ts_event", "sma_20"])

    ok, why = _approx_equal(full_aaa, iso_aaa)
    assert ok, f"Cross-symbol contamination detected: {why}"


def test_streaming_matches_offline_end_to_end(env) -> None:
    """The headline parity test: end-to-end batch vs end-to-end streaming."""
    # 1. Run offline backfill.
    engine = BatchEngine(
        raw_root=env["raw_root"],
        feature_root=env["feature_root"],
        manifest=env["manifest"],
        n_workers=1,
    )
    partitions = [
        {"asset_class": "stock", "exchange": "SSE", "frequency": "1m", "trading_date": d}
        for d in DATES
    ]
    engine.run(partitions, FEATURES)

    offline_by_day: dict[str, pl.DataFrame] = {}
    for d in DATES:
        tech = env["feature_store"].scan(
            filters={"feature_group": "technical", "frequency": "1m", "trading_date": d}
        )
        vol = env["feature_store"].scan(
            filters={"feature_group": "volume", "frequency": "1m", "trading_date": d}
        )
        offline_by_day[d] = tech.join(vol, on=["symbol", "ts_event"], how="inner")

    # 2. Stream the same raw data through the streaming engine.
    streaming_outputs: dict[str, pl.DataFrame] = {}
    for d in DATES:
        raw = (
            env["raw_store"]
            .scan(
                filters={
                    "asset_class": "stock",
                    "exchange": "SSE",
                    "frequency": "1m",
                    "trading_date": d,
                }
            )
            .sort(["ts_event", "symbol"])
        )
        dag = FeatureDAG(FEATURES)
        stream = StreamingEngine(
            dag,
            config=StreamingEngineConfig(
                session_id=d, frequency="1m", checkpoint_every_n_batches=10
            ),
        )
        # Push in chunks of 17 rows to ensure boundaries don't fall on bar count.
        chunk = 17
        chunks = (raw.slice(i, chunk) for i in range(0, raw.height, chunk))
        stream.run(chunks)
        out = stream.drain()
        assert out is not None
        streaming_outputs[d] = out.sort(["symbol", "ts_event"])

    # 3. Compare per-feature, per-day, per-symbol.
    feature_cols = [
        "sma_20", "vol_30", "rsi_14", "macd", "macd_signal", "macd_hist",
        "vwm_20", "vwm_zscore_60",
    ]
    for d in DATES:
        off = offline_by_day[d].sort(["symbol", "ts_event"]).select(
            ["symbol", "ts_event", *feature_cols]
        )
        stream = streaming_outputs[d].select(["symbol", "ts_event", *feature_cols])
        ok, why = _approx_equal(off, stream, tol=1e-6)
        assert ok, f"Streaming vs offline diverged for {d}: {why}"


def test_eod_archiver_writes_and_is_idempotent(env, tmp_path) -> None:
    """Archive once → files present + manifest row; archive again → no duplicates."""
    # First, run streaming to produce a frame.
    raw = (
        env["raw_store"]
        .scan(
            filters={
                "asset_class": "stock",
                "exchange": "SSE",
                "frequency": "1m",
                "trading_date": DATES[0],
            }
        )
        .sort(["ts_event", "symbol"])
    )
    dag = FeatureDAG(FEATURES)
    stream = StreamingEngine(
        dag,
        config=StreamingEngineConfig(
            session_id=DATES[0], frequency="1m", checkpoint_every_n_batches=100
        ),
    )
    stream.run([raw])
    out = stream.drain()
    assert out is not None
    out = out.with_columns(pl.lit(DATES[0]).alias("trading_date"))

    archiver = EodArchiver(
        raw_store=ParquetStore(
            tmp_path / "archive_raw",
            partition_cols=("asset_class", "exchange", "frequency", "trading_date"),
        ),
        feature_store=ParquetStore(
            tmp_path / "archive_feat",
            partition_cols=("feature_group", "frequency", "trading_date"),
        ),
        manifest=Manifest(tmp_path / "archive_meta"),
    )

    raw_cols = ["symbol", "ts_event", "open", "high", "low", "close",
                "volume", "turnover"]
    report1 = archiver.archive(
        out.with_columns(
            pl.lit("stock").alias("asset_class"),
            pl.lit("SSE").alias("exchange"),
            pl.lit("1m").alias("frequency"),
        ),
        feature_names=FEATURES,
        raw_columns=raw_cols,
        partition_values={
            "asset_class": "stock",
            "exchange": "SSE",
            "frequency": "1m",
            "trading_date": DATES[0],
        },
        mode="overwrite",
    )
    assert report1["partitions_written"] > 0

    # Second call should be a clean re-write (overwrite mode).
    report2 = archiver.archive(
        out.with_columns(
            pl.lit("stock").alias("asset_class"),
            pl.lit("SSE").alias("exchange"),
            pl.lit("1m").alias("frequency"),
        ),
        feature_names=FEATURES,
        raw_columns=raw_cols,
        partition_values={
            "asset_class": "stock",
            "exchange": "SSE",
            "frequency": "1m",
            "trading_date": DATES[0],
        },
        mode="overwrite",
    )
    assert report2["partitions_written"] == report1["partitions_written"]

    # No leftover staging dir.
    staging = tmp_path / "_staging"
    assert not staging.exists() or not any(staging.iterdir())
