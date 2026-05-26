"""Verify VWM checkpoint recovery against uninterrupted QuoteTick replay."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from math import isclose
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.aggregation import BarAggregationConfig
from nautilus_ext.aggregation import TickToBarAggregator
from nautilus_ext.data import CatalogQuoteTickSource
from nautilus_ext.data import bar_event_to_bar_input
from nautilus_ext.features import VwmFeatureConfig
from nautilus_ext.features import VwmFeatureEngine
from nautilus_ext.state import build_feature_state_store


DEFAULT_CATALOG_PATH = r"D:\QuanHub\DataHome\DataTrans\nautilus_catalog"
COMPARE_FIELDS = [
    "momentum",
    "vwm",
    "atr",
    "prev_vwm",
    "prev_atr",
    "bull_setup",
    "bear_setup",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check VWM feature state restore consistency.")
    parser.add_argument("--catalog-path", default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--instrument-id", default="IH2303.CFFEX")
    parser.add_argument("--interval", default="1min")
    parser.add_argument("--limit", type=int, default=4000)
    parser.add_argument("--split-events", type=int, default=2000)
    parser.add_argument("--state-backend", choices=["json", "redis"], default="json")
    parser.add_argument(
        "--state-output",
        default=r"outputs\state_restore_check\IH2303_CFFEX_1min_vwm_state.json",
    )
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument(
        "--redis-key",
        default="feature_state:vwm_short:IH2303.CFFEX:1min:restore_check",
    )
    parser.add_argument("--output-dir", default=r"outputs\state_restore_check")
    args = parser.parse_args()

    events = list(CatalogQuoteTickSource(args.catalog_path, args.instrument_id, limit=args.limit).iter_events())
    if not 0 < args.split_events < len(events):
        raise ValueError("split_events must be between 1 and the number of input events - 1.")

    continuous = _run_all(events, args.interval)
    store, state_key = _store_and_key(args)
    restored = None
    try:
        restored = _run_with_restore(events, args.split_events, args.interval, store, state_key)
        differences = _snapshot_differences(continuous, restored)
        passed = not differences
        print(f"input_ticks: {len(events)}")
        print(f"split_events: {args.split_events}")
        print(f"continuous_snapshot: {asdict(continuous)}")
        print(f"restored_snapshot: {asdict(restored)}")
        print(f"state_backend: {args.state_backend}")
        print(f"restore_check_passed={passed}")
        if differences:
            print(f"differences: {differences}")
            raise AssertionError("Restored feature output differs from uninterrupted output.")
    finally:
        if args.state_backend == "redis":
            store.delete(state_key)


def _run_all(events, interval):
    aggregator = TickToBarAggregator(BarAggregationConfig(interval=interval))
    engine = VwmFeatureEngine(VwmFeatureConfig())
    latest = _feed(events, aggregator, engine)
    return _finish(aggregator, engine, latest)


def _run_with_restore(events, split_events, interval, store, key):
    config = VwmFeatureConfig()
    first_aggregator = TickToBarAggregator(BarAggregationConfig(interval=interval))
    first_engine = VwmFeatureEngine(config)
    _feed(events[:split_events], first_aggregator, first_engine)
    store.save(
        key,
        {
            "feature_state": first_engine.state_dict(),
            "aggregator_state": first_aggregator.state_dict(),
        },
    )
    checkpoint = store.load(key)
    if checkpoint is None:
        raise ValueError("State checkpoint could not be loaded after save.")

    restored_aggregator = TickToBarAggregator(BarAggregationConfig(interval=interval))
    restored_engine = VwmFeatureEngine(config)
    restored_aggregator.load_state_dict(checkpoint["aggregator_state"])
    restored_engine.load_state_dict(checkpoint["feature_state"])
    latest = _feed(events[split_events:], restored_aggregator, restored_engine)
    return _finish(restored_aggregator, restored_engine, latest)


def _feed(events, aggregator, engine):
    latest = None
    for event in events:
        bar = aggregator.update(event)
        if bar is not None:
            latest = engine.update(bar_event_to_bar_input(bar))
            engine.set_last_ts_event(bar.ts_event)
    return latest


def _finish(aggregator, engine, latest):
    bar = aggregator.flush()
    if bar is not None:
        latest = engine.update(bar_event_to_bar_input(bar))
        engine.set_last_ts_event(bar.ts_event)
    if latest is None:
        raise ValueError("No bar features produced from the input events.")
    return latest


def _snapshot_differences(expected, actual):
    differences = {}
    for field in COMPARE_FIELDS:
        left = getattr(expected, field)
        right = getattr(actual, field)
        if isinstance(left, float) or isinstance(right, float):
            if left is None or right is None or not isclose(left, right, rel_tol=0.0, abs_tol=1e-9):
                differences[field] = {"continuous": left, "restored": right}
        elif left != right:
            differences[field] = {"continuous": left, "restored": right}
    return differences


def _store_and_key(args):
    if args.state_backend == "json":
        output = Path(args.state_output)
        return build_feature_state_store("json", json_root_dir=str(output.parent)), output.stem
    return build_feature_state_store("redis", redis_url=args.redis_url), args.redis_key


if __name__ == "__main__":
    main()
