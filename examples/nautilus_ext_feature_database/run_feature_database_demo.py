"""
Feature Database end-to-end demo.

Demonstrates the complete data flow without any real network connections:

    Mock bars  →  FeaturePipeline  →  OnlineFeatureStore (in-memory)
                                  →  OfflineFeatureStore (Parquet)
                                  →  FeatureManifest (JSON index)

Then reads back via:

    FeatureDataset       (training read path — Parquet)
    InferenceContext     (inference read path — in-memory)

No Nautilus Cython, no ccxt, no market data subscription required.

Usage
-----
    python examples/nautilus_ext_feature_database/run_feature_database_demo.py

    # Write output to a custom directory:
    python examples/nautilus_ext_feature_database/run_feature_database_demo.py \
        --output-dir /tmp/my_demo

Output
------
    outputs/examples/feature_database_demo/
        feature_manifest.json
        offline/
            demo_mom_v1/
                BTCUSDT-PERP_BINANCE/
                    <start>-<end>.parquet
"""
from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------------------
# Pure-Python demo feature engine (no Nautilus Cython dependency)
# ---------------------------------------------------------------------------

from nautilus_ext.features.feature_engine import FeatureEngineBase
from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec
from nautilus_ext.strategies.interfaces.input_types import BarInput

_DEMO_SCHEMA = FeatureSetSpec(
    feature_set_id="demo_mom_v1",
    version="1",
    input_types=["bar"],
    output_features=[
        FeatureFieldSpec("close_ma", "float", nullable=True,
                         description="Simple moving average of close prices."),
        FeatureFieldSpec("momentum", "float", nullable=True,
                         description="close[t] - close[t - mom_len]."),
        FeatureFieldSpec("vol_sum", "float", nullable=True,
                         description="Sum of volume over the lookback window."),
        FeatureFieldSpec("bar_count", "int", nullable=False,
                         description="Bars seen since reset."),
    ],
    required_history=20,
    frequency="1m",
    point_in_time_safe=True,
    description="Simple momentum + volume demo feature set (no Nautilus dependency).",
    owner="demo",
)


class DemoMomEngine(FeatureEngineBase):
    """Pure-Python momentum/volume feature engine for demo and CI use.

    Computes:
    - ``close_ma``  — SMA of the last *window* closes
    - ``momentum``  — close[t] - close[t - window]
    - ``vol_sum``   — rolling sum of volume over *window* bars
    - ``bar_count`` — bars seen since reset

    No Nautilus Cython required.  Suitable for tests and demo scripts.

    To add a real production engine, see:
        nautilus_ext/features/templates/example_feature_engine.py
    """

    def __init__(self, window: int = 20) -> None:
        self._window = window
        self._closes: deque[float] = deque(maxlen=window + 1)
        self._volumes: deque[float] = deque(maxlen=window)
        self._bar_count = 0

    @property
    def name(self) -> str:
        return "demo_mom_v1"

    @property
    def schema(self) -> FeatureSetSpec:
        return _DEMO_SCHEMA

    def reset(self) -> None:
        self._closes.clear()
        self._volumes.clear()
        self._bar_count = 0

    def update(self, event) -> FeatureEvent | None:
        if not isinstance(event, BarInput):
            return None
        self._closes.append(float(event.close))
        self._volumes.append(float(event.volume))
        self._bar_count += 1

        close_ma: float | None = None
        momentum: float | None = None
        vol_sum: float | None = None

        n = len(self._closes)
        if n >= self._window:
            closes = list(self._closes)
            close_ma = sum(closes[-self._window:]) / self._window
            momentum = closes[-1] - closes[-self._window]
            vol_sum = sum(self._volumes)

        return FeatureEvent(
            ts_event=event.ts_event,
            instrument_id=event.instrument_id,
            feature_set_id="demo_mom_v1",
            feature_version="1",
            values={
                "close_ma": close_ma,
                "momentum": momentum,
                "vol_sum": vol_sum,
                "bar_count": self._bar_count,
            },
            source_event_type="bar",
            source_event_ts=event.ts_event,
        )

    def state_dict(self) -> dict:
        return {
            "window": self._window,
            "closes": list(self._closes),
            "volumes": list(self._volumes),
            "bar_count": self._bar_count,
        }

    def load_state_dict(self, state: dict) -> None:
        self._window = state["window"]
        self._closes = deque(state["closes"], maxlen=self._window + 1)
        self._volumes = deque(state["volumes"], maxlen=self._window)
        self._bar_count = state["bar_count"]


# ---------------------------------------------------------------------------
# Mock data generator
# ---------------------------------------------------------------------------

def _make_mock_bars(
    n: int = 120,
    instrument_id: str = "BTCUSDT-PERP.BINANCE",
    start_ts_ms: int = 1_704_067_200_000,  # 2024-01-01 00:00:00 UTC
) -> list[BarInput]:
    """Generate n synthetic 1-minute BTCUSDT bars — no network required."""
    import math
    bars: list[BarInput] = []
    price = 42_000.0
    for i in range(n):
        ts = start_ts_ms + i * 60_000
        # Simple sine wave price motion + small random-like perturbation
        delta = math.sin(i * 0.15) * 80.0 + math.cos(i * 0.07) * 30.0
        price = max(10_000.0, price + delta)
        spread = price * 0.001
        open_ = price
        high = price + spread
        low = price - spread * 0.5
        close = price + delta * 0.1
        volume = 1.5 + abs(math.sin(i * 0.3)) * 3.0
        bars.append(BarInput(
            ts_event=ts,
            ts_init=ts,
            instrument_id=instrument_id,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            bar_type=f"{instrument_id}-1-MINUTE-LAST-EXTERNAL",
        ))
    return bars


# ---------------------------------------------------------------------------
# Demo runner
# ---------------------------------------------------------------------------

def run_demo(output_dir: Path) -> dict:
    """Run the full Feature Database demo and return a summary dict."""
    from nautilus_ext.features.feature_manifest import FeatureManifest
    from nautilus_ext.features.feature_pipeline import FeaturePipeline
    from nautilus_ext.features.feature_store import OfflineFeatureStore, OnlineFeatureStore
    from nautilus_ext.ml.feature_dataset import FeatureDatasetSpec, load_feature_dataset
    from nautilus_ext.ml.inference_context import ModelInferenceContext

    output_dir.mkdir(parents=True, exist_ok=True)

    instrument_id = "BTCUSDT-PERP.BINANCE"
    feature_set_id = "demo_mom_v1"
    warmup_bars = 20
    total_bars = 120

    # ------------------------------------------------------------------
    # 1. Build components
    # ------------------------------------------------------------------
    engine = DemoMomEngine(window=20)

    online_store = OnlineFeatureStore(window_size=200)
    offline_store = OfflineFeatureStore(
        base_path=output_dir,
        flush_threshold=total_bars + 1,   # manual flush at end
    )

    pipeline = FeaturePipeline(
        feature_engines=[engine],
        online_store=online_store,
        offline_store=offline_store,
    )

    # ------------------------------------------------------------------
    # 2. Generate mock bars
    # ------------------------------------------------------------------
    bars = _make_mock_bars(n=total_bars, instrument_id=instrument_id)
    warmup_bars_list = bars[:warmup_bars]
    live_bars_list = bars[warmup_bars:]

    # ------------------------------------------------------------------
    # 3. Warmup (is_warmup=True stamped by pipeline)
    # ------------------------------------------------------------------
    pipeline.warmup(warmup_bars_list)

    # ------------------------------------------------------------------
    # 4. Live updates — simulate real-time bar stream
    # ------------------------------------------------------------------
    generated_events: list[FeatureEvent] = []
    for bar in live_bars_list:
        events = pipeline.update(bar)
        generated_events.extend(events)

    # ------------------------------------------------------------------
    # 5. Flush to Parquet + update manifest
    # ------------------------------------------------------------------
    rows_written = pipeline.flush()

    # ------------------------------------------------------------------
    # 6. Online path: read latest feature from OnlineFeatureStore
    # ------------------------------------------------------------------
    latest_fe = online_store.get_latest(instrument_id, feature_set_id)
    online_latest = latest_fe.values if latest_fe is not None else {}

    # ------------------------------------------------------------------
    # 7. Offline path: inspect manifest
    # ------------------------------------------------------------------
    manifest = FeatureManifest(output_dir / "feature_manifest.json")
    manifest.load()
    manifest_records = len(manifest)
    offline_files = manifest.find_files(feature_set_id=feature_set_id,
                                         instrument_id=instrument_id)

    # ------------------------------------------------------------------
    # 8. Training read path: FeatureDataset
    # ------------------------------------------------------------------
    spec = FeatureDatasetSpec(
        feature_store_path=output_dir,
        feature_set_ids=[feature_set_id],
        instruments=[instrument_id],
        include_warmup=False,
    )
    df = load_feature_dataset(spec)

    # ------------------------------------------------------------------
    # 9. Inference read path: InferenceContext
    # ------------------------------------------------------------------
    ctx = ModelInferenceContext(
        online_store=online_store,
        feature_set_ids=[feature_set_id],
        feature_order=[
            f"{feature_set_id}.close_ma",
            f"{feature_set_id}.momentum",
            f"{feature_set_id}.vol_sum",
            f"{feature_set_id}.bar_count",
        ],
        missing_feature_policy="fill_none",
    )
    inference_vector = ctx.get_feature_vector(instrument_id)

    # ------------------------------------------------------------------
    # 10. Summary
    # ------------------------------------------------------------------
    summary = {
        "generated_events": len(generated_events),
        "online_latest_feature": online_latest,
        "offline_files": offline_files,
        "manifest_records": manifest_records,
        "dataset_shape": tuple(df.shape),
        "dataset_columns": list(df.columns),
        "inference_vector_keys": list(inference_vector.keys()),
        "inference_vector_values": list(inference_vector.values()),
        "rows_flushed_to_parquet": rows_written,
    }
    return summary


def _print_summary(summary: dict) -> None:
    print("\n" + "=" * 60)
    print("Feature Database Demo — Summary")
    print("=" * 60)
    print(f"  generated_events      : {summary['generated_events']}")
    print(f"  rows_flushed_parquet  : {summary['rows_flushed_to_parquet']}")
    print(f"  manifest_records      : {summary['manifest_records']}")
    print(f"  offline_files         : {summary['offline_files']}")
    print(f"  dataset_shape         : {summary['dataset_shape']}")
    print(f"  dataset_columns       : {summary['dataset_columns']}")
    print(f"  inference_vector_keys : {summary['inference_vector_keys']}")
    print()
    print("  online_latest_feature:")
    for k, v in summary["online_latest_feature"].items():
        print(f"    {k:<15} = {v}")
    print()
    print("  inference_vector:")
    for k, v in zip(summary["inference_vector_keys"], summary["inference_vector_values"]):
        print(f"    {k:<40} = {v}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Feature Database end-to-end demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/examples/feature_database_demo",
        help="Root directory for demo output files (default: outputs/examples/feature_database_demo)",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    print(f"Running Feature Database demo → {output_dir.resolve()}")
    summary = run_demo(output_dir)
    _print_summary(summary)
    print(f"\nOutput written to: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
