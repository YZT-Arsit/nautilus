"""Replay QuoteTicks incrementally into bars and VWM features.

This is a replay stream, not a live WebSocket connector. Bar volume is
synthetic quote tick_count and is not valid traded-volume evidence.
"""

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
from nautilus_ext.pipelines import StreamFeaturePipeline
from nautilus_ext.state import JsonFeatureStateStore


DEFAULT_CATALOG_PATH = r"D:\QuanHub\DataHome\DataTrans\nautilus_catalog"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay-stream VWM features from QuoteTicks.")
    parser.add_argument("--catalog-path", default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--instrument-id", default="IH2303.CFFEX")
    parser.add_argument("--interval", default="1min")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--output-dir", default="outputs/flow_stream_features")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    pipeline = StreamFeaturePipeline(
        event_source=CatalogQuoteTickSource(args.catalog_path, args.instrument_id, limit=args.limit),
        bar_aggregator=TickToBarAggregator(BarAggregationConfig(interval=args.interval)),
        feature_engine=VwmFeatureEngine(VwmFeatureConfig()),
        state_store=JsonFeatureStateStore(str(output_dir / "states")),
        state_key=f"{args.instrument_id}_{args.interval}_vwm_replay",
        save_every_bars=10,
    )
    records = pipeline.run()
    summary_path = _write_summary(output_dir, records, pipeline)

    print("stream_mode: replay_stream")
    print(f"processed_event_count: {pipeline.processed_events}")
    print(f"emitted_bar_count: {len(records)}")
    if records:
        print(f"first_bar_time: {records[0].ts_event}")
        print(f"last_bar_time: {records[-1].ts_event}")
        print(f"latest_snapshot: {asdict(records[-1].snapshot)}")
    print("volume_type: synthetic_tick_count")
    print("WARNING: replay stream only; synthetic_tick_count is not traded volume.")
    print(f"state_path: {pipeline.last_state_path}")
    print(f"summary_path: {summary_path}")


def _write_summary(output_dir: Path, records, pipeline) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "stream_vwm_feature_summary.json"
    payload = {
        "stream_mode": "replay_stream",
        "processed_events": pipeline.processed_events,
        "emitted_bars": pipeline.emitted_bars,
        "volume_type": "synthetic_tick_count",
        "first_bar_time": records[0].ts_event.isoformat() if records else None,
        "last_bar_time": records[-1].ts_event.isoformat() if records else None,
        "latest_snapshot": asdict(records[-1].snapshot) if records else None,
        "state_path": str(pipeline.last_state_path),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    main()
