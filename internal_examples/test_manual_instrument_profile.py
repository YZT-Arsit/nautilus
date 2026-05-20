#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import AutoInstrumentProfileBuilder


DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)

profile = AutoInstrumentProfileBuilder.build_profile(
    symbol="BCHUSDT",
    data_root=DATA_ROOT,
    instrument_type="crypto_perpetual",
    venue="BINANCE",
)

assert profile.instrument_type == "crypto_perpetual"
assert profile.venue == "BINANCE"
assert "BCHUSDT" in profile.instrument_id

print("manual instrument profile ok")
