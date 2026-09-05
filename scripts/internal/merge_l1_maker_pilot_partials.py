#!/usr/bin/env python3
"""Merge three independently executed symbol partitions into the pilot root."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_pilot"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TABLES = (
    "l1_maker_execution_metrics.csv",
    "trade_only_maker_execution_metrics.csv",
    "l1_fill_model_sensitivity.csv",
    "maker_model_comparison.csv",
    "maker_orders.csv",
    "maker_fills.csv",
    "spread_statistics.csv",
    "markout_statistics.csv",
)


def main() -> None:
    for name in TABLES:
        frames = [pd.read_csv(OUTPUT / f"run_{symbol}" / name) for symbol in SYMBOLS]
        destination = OUTPUT / name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        pd.concat(frames, ignore_index=True).to_csv(temporary, index=False)
        os.replace(temporary, destination)
    for directory in ("figures", "paths"):
        destination = OUTPUT / directory
        destination.mkdir(parents=True, exist_ok=True)
        for symbol in SYMBOLS:
            for source in (OUTPUT / f"run_{symbol}" / directory).glob("*"):
                shutil.copy2(source, destination / source.name)


if __name__ == "__main__":
    main()
