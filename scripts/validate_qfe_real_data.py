"""Real-data validation: offline vs streaming parity on catalog-derived bars.

Mirrors :mod:`scripts.validate_qfe_mvp` but consumes **real** bars produced
by :mod:`internal_examples.build_qfe_raw_from_catalog`. The goal is identical:
prove the framework produces byte-identical features whether driven offline or
streaming, but this time over data the user actually trades on.

Workflow
--------
1. Scan the Hive raw layout for one ``(asset_class, exchange, frequency,
   instrument)`` set.
2. Run :class:`BatchEngine` over each trading_date partition the bridge wrote.
3. Replay the same partitions through :class:`StreamingEngine` in micro-batches.
4. Diff the feature frames per (date, symbol) and print ``PASS`` / ``FAIL``.

Usage
-----
::

    python scripts/validate_qfe_real_data.py \\
        --raw-root D:\\nautilus\\data\\raw \\
        --feature-root D:\\nautilus\\data\\features \\
        --manifest-root D:\\nautilus\\data\\_meta \\
        --instrument-id IH2303.CFFEX --asset-class futures --exchange CFFEX
"""
from __future__ import annotations

import argparse
import math
import platform
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _print_env_header() -> None:
    print("=" * 70)
    print("quant_feature_engine REAL-DATA validation")
    print("=" * 70)
    print(f"OS:           {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python:       {sys.version.split()[0]} ({sys.executable})")
    print(f"Project root: {PROJECT_ROOT}")


_print_env_header()

import polars as pl  # noqa: E402
import pyarrow as pa  # noqa: E402

from quant_feature_engine.core.dag import FeatureDAG  # noqa: E402
from quant_feature_engine.execution.batch_engine import BatchEngine  # noqa: E402
from quant_feature_engine.features import load_all  # noqa: E402
from quant_feature_engine.storage.metadata import Manifest  # noqa: E402
from quant_feature_engine.storage.parquet_store import ParquetStore  # noqa: E402
from quant_feature_engine.streaming.engine import (  # noqa: E402
    StreamingEngine,
    StreamingEngineConfig,
)

print(f"polars:       {pl.__version__}")
print(f"pyarrow:      {pa.__version__}")

load_all()

# Sub-set of the built-in feature set that is meaningful at low warm-up.
FEATURES = ["sma_20", "vol_30", "rsi_14", "macd", "vwm_20", "vwm_zscore_60"]
FEATURE_COLS = [
    "sma_20", "vol_30", "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "vwm_20", "vwm_zscore_60",
]
TOLERANCE = 1e-6
DEFAULT_CHUNK = 17


def _diff_frames(off: pl.DataFrame, stream: pl.DataFrame) -> tuple[bool, str]:
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


def _list_trading_dates(
    raw_store: ParquetStore,
    *,
    asset_class: str,
    exchange: str,
    frequency: str,
    instrument_id: str,
) -> list[str]:
    """Walk the Hive tree and find dates that actually contain rows for this symbol."""
    root = raw_store.root / f"asset_class={asset_class}" / f"exchange={exchange}" / f"frequency={frequency}"
    if not root.exists():
        return []
    dates: list[str] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("trading_date="):
            continue
        date_str = d.name.split("=", 1)[1]
        # Confirm the file has rows for this instrument (catalog may co-locate symbols).
        sample = raw_store.scan(
            filters={
                "asset_class": asset_class,
                "exchange": exchange,
                "frequency": frequency,
                "trading_date": date_str,
            },
            columns=["symbol"],
        )
        if sample.is_empty():
            continue
        if instrument_id in sample["symbol"].unique().to_list():
            dates.append(date_str)
    return dates


def _run_offline(
    *,
    raw_store: ParquetStore,
    feature_root: Path,
    manifest_root: Path,
    asset_class: str,
    exchange: str,
    frequency: str,
    dates: list[str],
) -> dict[str, pl.DataFrame]:
    feat_store = ParquetStore(
        feature_root,
        partition_cols=("feature_group", "frequency", "trading_date"),
    )
    manifest = Manifest(manifest_root)
    engine = BatchEngine(
        raw_root=raw_store.root,
        feature_root=feature_root,
        manifest=manifest,
        n_workers=1,
    )
    partitions = [
        {
            "asset_class": asset_class,
            "exchange": exchange,
            "frequency": frequency,
            "trading_date": d,
        }
        for d in dates
    ]
    results = engine.run(partitions, FEATURES, force=True)
    print(f"           offline rows: {sum(r.get('rows', 0) for r in results)}")
    out: dict[str, pl.DataFrame] = {}
    for d in dates:
        tech = feat_store.scan(
            filters={"feature_group": "technical", "frequency": frequency, "trading_date": d}
        )
        vol = feat_store.scan(
            filters={"feature_group": "volume", "frequency": frequency, "trading_date": d}
        )
        out[d] = tech.join(vol, on=["symbol", "ts_event"], how="inner")
    return out


def _run_streaming(
    *,
    raw_store: ParquetStore,
    asset_class: str,
    exchange: str,
    frequency: str,
    instrument_id: str,
    dates: list[str],
    chunk_size: int,
) -> dict[str, pl.DataFrame]:
    out: dict[str, pl.DataFrame] = {}
    for d in dates:
        raw = (
            raw_store.scan(
                filters={
                    "asset_class": asset_class,
                    "exchange": exchange,
                    "frequency": frequency,
                    "trading_date": d,
                }
            )
            .filter(pl.col("symbol") == instrument_id)
            .sort(["ts_event", "symbol"])
        )
        if raw.is_empty():
            print(f"           [WARN] {d}: no rows for {instrument_id}, skipping streaming")
            continue
        dag = FeatureDAG(FEATURES)
        engine = StreamingEngine(
            dag,
            config=StreamingEngineConfig(
                session_id=d, frequency=frequency, checkpoint_every_n_batches=10
            ),
        )
        chunks = (raw.slice(i, chunk_size) for i in range(0, raw.height, chunk_size))
        engine.run(chunks)
        frame = engine.drain()
        assert frame is not None
        out[d] = frame.sort(["symbol", "ts_event"])
        print(
            f"           {d}: batches={engine.stats.batches} "
            f"rows={engine.stats.rows} errors={engine.stats.errors}"
        )
    return out


def _compare(
    *,
    offline: dict[str, pl.DataFrame],
    streaming: dict[str, pl.DataFrame],
    instrument_id: str,
) -> bool:
    all_ok = True
    for d, stream in streaming.items():
        off = offline.get(d)
        if off is None or off.is_empty():
            print(f"           [BAD] {d}: no offline output to compare")
            all_ok = False
            continue
        off_sym = (
            off.filter(pl.col("symbol") == instrument_id)
            .sort(["symbol", "ts_event"])
            .select(["symbol", "ts_event", *FEATURE_COLS])
        )
        stream_sym = stream.select(["symbol", "ts_event", *FEATURE_COLS])
        ok, why = _diff_frames(off_sym, stream_sym)
        status = "OK " if ok else "BAD"
        print(f"           [{status}] {d}: rows={off_sym.height}  reason={why}")
        all_ok &= ok
    return all_ok


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw-root", required=True, type=Path)
    p.add_argument("--instrument-id", required=True)
    p.add_argument("--asset-class", default="futures")
    p.add_argument("--exchange", default="CFFEX")
    p.add_argument("--frequency", default="1m")
    p.add_argument("--feature-root", type=Path, default=None,
                   help="If omitted, a temp dir is used so the catalog stays clean.")
    p.add_argument("--manifest-root", type=Path, default=None)
    p.add_argument(
        "--chunk-sizes",
        default=str(DEFAULT_CHUNK),
        help="Comma-separated list of chunk sizes to test (e.g. '17,113'). "
             "Streaming is rerun once per size and each run is compared back "
             "to the same offline output — different chunk sizes must all "
             "yield identical features. This is the chunk-boundary robustness "
             "check.",
    )
    args = p.parse_args()
    chunk_sizes = [int(x.strip()) for x in args.chunk_sizes.split(",") if x.strip()]
    if not chunk_sizes:
        print("FAIL --chunk-sizes parsed empty")
        return 1

    raw_store = ParquetStore(
        args.raw_root,
        partition_cols=("asset_class", "exchange", "frequency", "trading_date"),
    )

    dates = _list_trading_dates(
        raw_store,
        asset_class=args.asset_class,
        exchange=args.exchange,
        frequency=args.frequency,
        instrument_id=args.instrument_id,
    )
    if not dates:
        print(f"\nFAIL no raw partitions found for {args.instrument_id} under "
              f"{args.raw_root}")
        return 1
    print(f"\n[step 1/3] discovered {len(dates)} trading_date partition(s) "
          f"for {args.instrument_id}: {dates}")

    feat_ctx: tempfile.TemporaryDirectory | None = None
    meta_ctx: tempfile.TemporaryDirectory | None = None
    feature_root = args.feature_root
    manifest_root = args.manifest_root
    if feature_root is None:
        feat_ctx = tempfile.TemporaryDirectory(prefix="qfe-real-feat-")
        feature_root = Path(feat_ctx.name)
    if manifest_root is None:
        meta_ctx = tempfile.TemporaryDirectory(prefix="qfe-real-meta-")
        manifest_root = Path(meta_ctx.name)

    try:
        print(f"[step 2/3] offline backfill (feature_root={feature_root}) ...")
        offline = _run_offline(
            raw_store=raw_store,
            feature_root=feature_root,
            manifest_root=manifest_root,
            asset_class=args.asset_class,
            exchange=args.exchange,
            frequency=args.frequency,
            dates=dates,
        )
        overall_ok = True
        per_chunk_summaries: list[str] = []
        for cs in chunk_sizes:
            print(f"[step 3/3] streaming replay (chunk_size={cs}) ...")
            streaming = _run_streaming(
                raw_store=raw_store,
                asset_class=args.asset_class,
                exchange=args.exchange,
                frequency=args.frequency,
                instrument_id=args.instrument_id,
                dates=dates,
                chunk_size=cs,
            )
            print(f"[compare] chunk_size={cs}")
            print("-" * 70)
            ok = _compare(
                offline=offline,
                streaming=streaming,
                instrument_id=args.instrument_id,
            )
            per_chunk_summaries.append(
                f"chunk_size={cs}: {'PASS' if ok else 'FAIL'} "
                f"({len(streaming)} date(s))"
            )
            overall_ok &= ok

        print()
        print("=" * 70)
        for line in per_chunk_summaries:
            print("  " + line)
        print("=" * 70)
        if overall_ok:
            print(
                f"PASS — features match for {args.instrument_id} across "
                f"{len(dates)} trading_date(s) and {len(chunk_sizes)} chunk size(s)"
            )
            return 0
        print("FAIL — see [BAD] lines above")
        return 1
    finally:
        if feat_ctx is not None:
            feat_ctx.cleanup()
        if meta_ctx is not None:
            meta_ctx.cleanup()


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    raise SystemExit(main())
