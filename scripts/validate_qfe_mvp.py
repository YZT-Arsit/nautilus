"""End-to-end validation harness for the quant_feature_engine MVP.

This is the canonical "does it actually work?" smoke test you run after a
fresh checkout or a dependency install. It is **self-contained**:
  * generates synthetic OHLCV for 2 symbols × 2 trading dates,
  * writes the raw data to a Hive-style Parquet layout in a temp directory,
  * runs the offline :class:`BatchEngine` to compute features,
  * replays the same data through the :class:`StreamingEngine` in micro-
    batches of 17 rows (an awkward size on purpose, so any boundary bugs
    surface),
  * diffs the two output frames row-by-row,
  * prints ``PASS`` (exit 0) or ``FAIL`` (exit 1).

Usage::

    python scripts/validate_qfe_mvp.py

Designed to run on Linux, macOS, **and Windows** — all paths go through
``pathlib`` and ``tempfile`` so Hive partition separators stay native.
"""
from __future__ import annotations

import math
import os
import platform
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Header — print before we import polars so a missing dep fails loudly
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _print_env_header() -> None:
    print("=" * 70)
    print("quant_feature_engine MVP validation")
    print("=" * 70)
    print(f"OS:            {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python:        {sys.version.split()[0]}  ({sys.executable})")
    print(f"Project root:  {PROJECT_ROOT}")
    print(f"CWD:           {Path.cwd()}")


_print_env_header()

# Make the in-repo package importable without an install.
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import numpy as np
    import polars as pl
    import pyarrow as pa
except ImportError as e:
    print(f"\n[FAIL] missing dependency: {e}")
    print("  install with: pip install -r quant_feature_engine/requirements.txt")
    sys.exit(1)

print(f"polars:        {pl.__version__}")
print(f"pyarrow:       {pa.__version__}")
print(f"numpy:         {np.__version__}")
print()

# Framework imports (must succeed after deps are present).
from feature_engine.core.dag import FeatureDAG  # noqa: E402
from feature_engine.execution.batch_engine import BatchEngine  # noqa: E402
from feature_engine.features import load_all  # noqa: E402
from feature_engine.storage.metadata import Manifest  # noqa: E402
from feature_engine.storage.parquet_store import ParquetStore  # noqa: E402
from feature_engine.streaming.engine import (  # noqa: E402
    StreamingEngine,
    StreamingEngineConfig,
)

load_all()

# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------

SYMBOLS = ("AAA", "BBB")
DATES = ("2026-05-25", "2026-05-26")
BARS_PER_DAY = 240
FEATURES = ["sma_20", "vol_30", "rsi_14", "macd", "vwm_20", "vwm_zscore_60"]
FEATURE_COLS = [
    "sma_20", "vol_30", "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "vwm_20", "vwm_zscore_60",
]
CHUNK_SIZE = 17  # deliberately not a divisor of BARS_PER_DAY
TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_day(symbol: str, date_str: str, seed: int) -> pl.DataFrame:
    """One symbol-day of synthetic minute bars."""
    rng = np.random.default_rng(seed)
    start = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    price = 100.0 + (10.0 if symbol == "BBB" else 0.0)
    rows = []
    for i in range(BARS_PER_DAY):
        price *= 1 + rng.normal(0, 0.0008)
        ts = start + timedelta(minutes=i)
        rows.append(
            {
                "symbol": symbol,
                "ts_event": ts,
                "open": price * (1 - 0.0003),
                "high": price * 1.0008,
                "low": price * 0.9992,
                "close": price,
                "volume": float(rng.integers(1_000, 10_000)),
                "turnover": price * 5_000.0,
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
        frames = []
        for sym in SYMBOLS:
            seed += 1
            frames.append(_make_day(sym, date_str, seed))
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


def _diff_frames(off: pl.DataFrame, stream: pl.DataFrame) -> tuple[bool, str]:
    """Row-by-row diff with None/NaN awareness."""
    if off.shape != stream.shape:
        return False, f"shape mismatch: {off.shape} vs {stream.shape}"
    if list(off.columns) != list(stream.columns):
        return False, f"column mismatch: {off.columns} vs {stream.columns}"
    for col in off.columns:
        xs, ys = off[col].to_list(), stream[col].to_list()
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
                if abs(x - y) > TOLERANCE:
                    return False, f"{col}[{i}] {x} vs {y} (diff {abs(x-y)})"
            elif x != y:
                return False, f"{col}[{i}] {x!r} vs {y!r}"
    return True, "ok"


# ---------------------------------------------------------------------------
# Validation steps
# ---------------------------------------------------------------------------


def _run_offline(tmp: Path, raw_store: ParquetStore) -> dict[str, pl.DataFrame]:
    print(f"[step 1/4] offline backfill: {len(DATES)} partitions, "
          f"{len(FEATURES)} features ...")
    feat_root = tmp / "features"
    manifest = Manifest(tmp / "_meta")
    feat_store = ParquetStore(
        feat_root,
        partition_cols=("feature_group", "frequency", "trading_date"),
    )
    engine = BatchEngine(
        raw_root=raw_store.root,
        feature_root=feat_root,
        manifest=manifest,
        n_workers=1,
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
    total_rows = sum(r.get("rows", 0) for r in results)
    print(f"           offline rows: {total_rows}, manifest rows: "
          f"{sum(len(r.get('manifest_rows', [])) for r in results)}")

    offline: dict[str, pl.DataFrame] = {}
    for d in DATES:
        tech = feat_store.scan(
            filters={"feature_group": "technical", "frequency": "1m", "trading_date": d}
        )
        vol = feat_store.scan(
            filters={"feature_group": "volume", "frequency": "1m", "trading_date": d}
        )
        offline[d] = tech.join(vol, on=["symbol", "ts_event"], how="inner")
    return offline


def _run_streaming(raw_store: ParquetStore) -> dict[str, pl.DataFrame]:
    print(f"[step 2/4] streaming replay: chunk size {CHUNK_SIZE} rows ...")
    out: dict[str, pl.DataFrame] = {}
    for d in DATES:
        raw = (
            raw_store.scan(
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
        engine = StreamingEngine(
            dag,
            config=StreamingEngineConfig(
                session_id=d, frequency="1m", checkpoint_every_n_batches=10
            ),
        )
        chunks = (raw.slice(i, CHUNK_SIZE) for i in range(0, raw.height, CHUNK_SIZE))
        engine.run(chunks)
        frame = engine.drain()
        assert frame is not None, f"empty streaming output for {d}"
        out[d] = frame.sort(["symbol", "ts_event"])
        print(f"           {d}: batches={engine.stats.batches} "
              f"rows={engine.stats.rows} errors={engine.stats.errors}")
    return out


def _compare(
    offline: dict[str, pl.DataFrame],
    streaming: dict[str, pl.DataFrame],
) -> bool:
    print(f"[step 3/4] comparing offline vs streaming (tol={TOLERANCE}) ...")
    all_ok = True
    for d in DATES:
        off = (
            offline[d]
            .sort(["symbol", "ts_event"])
            .select(["symbol", "ts_event", *FEATURE_COLS])
        )
        stream = streaming[d].select(["symbol", "ts_event", *FEATURE_COLS])
        ok, why = _diff_frames(off, stream)
        status = "OK " if ok else "BAD"
        print(f"           [{status}] {d}: rows={off.height}  reason={why}")
        all_ok &= ok
    return all_ok


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qfe-validate-") as td:
        tmp = Path(td)
        print(f"[step 0/4] writing synthetic OHLCV to {tmp / 'raw'} ...")
        raw_store = _write_raw(tmp / "raw")
        print(f"           {len(SYMBOLS)} symbols × {len(DATES)} dates × "
              f"{BARS_PER_DAY} bars = "
              f"{len(SYMBOLS)*len(DATES)*BARS_PER_DAY} total rows")

        offline = _run_offline(tmp, raw_store)
        streaming = _run_streaming(raw_store)
        passed = _compare(offline, streaming)

        print("[step 4/4] result")
        print("-" * 70)
        if passed:
            print("PASS — offline backfill and streaming replay produce identical features")
            return 0
        print("FAIL — see [BAD] lines above for the first row that diverged")
        return 1


if __name__ == "__main__":
    # Force flush so the harness output is visible even when redirected.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    raise SystemExit(main())
