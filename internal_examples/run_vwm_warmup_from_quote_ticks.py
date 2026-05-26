"""Warm up VWM feature state from historical QuoteTicks without placing orders."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.aggregation import BarAggregationConfig
from nautilus_ext.aggregation import TickToBarAggregator
from nautilus_ext.data import CatalogQuoteTickSource
from nautilus_ext.features import VwmFeatureConfig
from nautilus_ext.features import VwmFeatureEngine
from nautilus_ext.pipelines import FeatureWarmupPipeline
from nautilus_ext.state import build_feature_state_store


DEFAULT_CATALOG_PATH = r"D:\QuanHub\DataHome\DataTrans\nautilus_catalog"


def main() -> None:
    parser = argparse.ArgumentParser(description="Warm up VWM feature state from QuoteTicks.")
    parser.add_argument("--catalog-path", default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--instrument-id", default="IH2303.CFFEX")
    parser.add_argument("--interval", default="1min")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--state-backend", choices=["json", "redis"], default="json")
    parser.add_argument(
        "--state-output",
        default=r"outputs\feature_states\IH2303_CFFEX_1min_vwm_state.json",
    )
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--redis-key", default="feature_state:vwm_short:IH2303.CFFEX:1min")
    parser.add_argument("--output-dir", default=r"outputs\feature_states")
    args = parser.parse_args()

    engine = VwmFeatureEngine(VwmFeatureConfig())
    summary = FeatureWarmupPipeline(
        CatalogQuoteTickSource(args.catalog_path, args.instrument_id, limit=args.limit),
        TickToBarAggregator(BarAggregationConfig(interval=args.interval)),
        engine,
    ).run()
    store, state_key = _store_and_key(args)
    saved_at = store.save(
        state_key,
        {
            "feature_state": summary.feature_state,
            "aggregator_state": summary.aggregator_state,
            "processed_events": summary.processed_events,
            "emitted_bars": summary.emitted_bars,
            "volume_type": "synthetic_tick_count",
        },
    )

    print(f"processed_events: {summary.processed_events}")
    print(f"emitted_bars: {summary.emitted_bars}")
    print(f"first_bar_time: {summary.first_bar_time}")
    print(f"last_bar_time: {summary.last_bar_time}")
    print(f"latest_snapshot: {asdict(summary.latest_snapshot) if summary.latest_snapshot else None}")
    print(f"state_backend: {args.state_backend}")
    print(f"state_location: {saved_at}")
    print("volume_type: synthetic_tick_count")
    print("WARNING: warmup only; synthetic_tick_count is not traded volume.")


def _store_and_key(args):
    if args.state_backend == "json":
        output = Path(args.state_output)
        return build_feature_state_store("json", json_root_dir=str(output.parent)), output.stem
    return build_feature_state_store("redis", redis_url=args.redis_url), args.redis_key


if __name__ == "__main__":
    main()
