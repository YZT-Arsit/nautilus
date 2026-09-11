#!/usr/bin/env python3
"""Experiment-local nine-symbol entry point for validated streaming L1 ingest."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal import acquire_l1_maker_pilot_data as ingest


ingest.SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)


if __name__ == "__main__":
    ingest.main()
