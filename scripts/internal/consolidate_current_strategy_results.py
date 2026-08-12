#!/usr/bin/env python3
"""Add validated native-tick runs to the canonical result hierarchy."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd
import yaml


TICK_CASES = (
    ("lag_0s", "lag0event", "0 ns; first following TradeEvent"),
    ("lag_60s", "lag60s", "60s physical-time"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--tick-source", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary_path = args.canonical_root / "canonical_summary.csv"
    summary = pd.read_csv(summary_path)
    summary = summary[summary["granularity"] != "native trade tick"].copy()
    rows = []
    for source_case, destination_case, lag_label in TICK_CASES:
        source = args.tick_source / source_case / "normal" / "nofee"
        metrics = json.loads(
            (source / "strategy_evaluation_metrics.json").read_text(encoding="utf-8")
        )
        validation = json.loads(
            (source / "strategy_evaluation_validation.json").read_text(encoding="utf-8")
        )
        if not all(validation.values()):
            raise ValueError(f"tick validation failed: {source_case}")
        config = yaml.safe_load((source / "config.yaml").read_text(encoding="utf-8")) or {}
        destination = (
            args.canonical_root
            / "continuous_tick_ma"
            / "BTCUSDT"
            / "tick"
            / destination_case
        )
        destination.mkdir(parents=True, exist_ok=True)
        figure_name = f"BTCUSDT_tick_{destination_case}_performance.png"
        shutil.copy2(source / "charts" / "strategy_evaluation.png", destination / figure_name)
        for name in (
            "config.yaml",
            "strategy_evaluation.parquet",
            "strategy_evaluation_metrics.json",
            "strategy_evaluation_validation.json",
        ):
            shutil.copy2(source / name, destination / name)
        provenance = {
            "strategy": "continuous_tick_ma",
            "symbol": "BTCUSDT",
            "granularity": "native trade tick",
            "tick_definition": "one Binance raw futures trade / TradeEvent",
            "lag": lag_label,
            "source_run": str(source.resolve()),
            "figure": str((destination / figure_name).resolve()),
            "validation": validation,
            "five_year_status": "pending normalized-tick completion",
        }
        (destination / "provenance.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
        )
        start = config.get("start") or "2021-07-01"
        end = config.get("end") or "2021-07-07"
        for premium in ("included", "excluded"):
            values = metrics["cases"][premium]
            rows.append(
                {
                    "strategy": "continuous_tick_ma",
                    "symbol": "BTCUSDT",
                    "granularity": "native trade tick",
                    "lag": lag_label,
                    "premium": premium,
                    "final_return_1x": values["final_return_1x"],
                    "turnover": values["turnover"],
                    "break_even_bps": values["break_even_bps"],
                    "max_drawdown": values["max_drawdown"],
                    "start_time": start,
                    "end_time": end,
                    "source_timeseries": str((source / "strategy_evaluation.parquet").resolve()),
                    "figure": str((destination / figure_name).resolve()),
                }
            )
    combined = pd.concat([summary, pd.DataFrame(rows)], ignore_index=True).sort_values(
        ["strategy", "granularity", "lag", "premium"]
    )
    temporary = summary_path.with_suffix(".csv.tmp")
    combined.to_csv(temporary, index=False)
    temporary.replace(summary_path)
    (args.canonical_root / "canonical_summary.html").write_text(
        combined.to_html(index=False, float_format=lambda value: f"{value:.8f}"),
        encoding="utf-8",
    )
    manifest_path = args.canonical_root / "artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        native_tick_strategy_count=1,
        native_tick_case_count=2,
        native_tick_figure_count=2,
        summary_rows=len(combined),
        native_tick_scope="validated 7-day lag comparison; five-year pending",
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"CONSOLIDATED rows={len(combined)} tick_figures=2 root={args.canonical_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
