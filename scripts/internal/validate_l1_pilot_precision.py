#!/usr/bin/env python3
"""Validate archived March-2024 BBO/size values against frozen increments."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_pilot"
PRICE_INCREMENT = {"BTCUSDT": 0.01, "ETHUSDT": 0.01, "SOLUSDT": 0.001}
SIZE_INCREMENT = {"BTCUSDT": 0.001, "ETHUSDT": 0.001, "SOLUSDT": 0.1}


def misaligned(values: np.ndarray, increment: float) -> int:
    scaled = values / increment
    return int(np.count_nonzero(np.abs(scaled - np.rint(scaled)) > 1e-6))


def main() -> None:
    rows: list[dict] = []
    for symbol in PRICE_INCREMENT:
        price_bad = size_bad = observations = 0
        price_bad_examples: list[float] = []
        price_bad_dates: set[str] = set()
        for path in sorted((OUTPUT / "l1_quotes" / f"symbol={symbol}").glob("date=*/part.parquet")):
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(
                columns=["bid_price", "ask_price", "bid_size", "ask_size"],
                batch_size=1_000_000,
            ):
                frame = batch.to_pandas()
                for column in ("bid_price", "ask_price"):
                    values = frame[column].to_numpy(float)
                    scaled = values / PRICE_INCREMENT[symbol]
                    bad = np.abs(scaled - np.rint(scaled)) > 1e-6
                    price_bad += int(np.count_nonzero(bad))
                    if np.any(bad):
                        price_bad_dates.add(path.parent.name.removeprefix("date="))
                        price_bad_examples.extend(values[bad][: 10 - len(price_bad_examples)].tolist())
                size_bad += misaligned(frame.bid_size.to_numpy(float), SIZE_INCREMENT[symbol])
                size_bad += misaligned(frame.ask_size.to_numpy(float), SIZE_INCREMENT[symbol])
                observations += len(frame) * 2
        rows.append(
            {
                "symbol": symbol,
                "period": "[2024-03-01,2024-03-31)",
                "historical_price_increment": PRICE_INCREMENT[symbol],
                "historical_size_increment": SIZE_INCREMENT[symbol],
                "price_observations": observations,
                "price_increment_mismatches": price_bad,
                "price_increment_mismatch_dates": ";".join(sorted(price_bad_dates)),
                "price_increment_mismatch_examples": ";".join(map(str, price_bad_examples)),
                "size_observations": observations,
                "size_increment_mismatches": size_bad,
                "status": "PASSED" if price_bad == 0 and size_bad == 0 else "FAILED",
            }
        )
    destination = OUTPUT / "instrument_precision_validation.csv"
    temporary = destination.with_suffix(".csv.tmp")
    pd.DataFrame(rows).to_csv(temporary, index=False)
    os.replace(temporary, destination)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
