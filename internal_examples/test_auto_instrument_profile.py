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
SYMBOL = "BCHUSDT"

profile = AutoInstrumentProfileBuilder.build_profile(symbol=SYMBOL, data_root=DATA_ROOT)

print(f"instrument_type: {profile.instrument_type}")
print(f"venue: {profile.venue}")
print(f"instrument_id: {profile.instrument_id}")
print(f"price_precision: {profile.price_precision}")
print(f"size_precision: {profile.size_precision}")
print(f"price_increment: {profile.price_increment}")
print(f"size_increment: {profile.size_increment}")
print(f"confidence: {profile.confidence}")
print(f"source: {profile.source}")

assert profile.instrument_type == "crypto_perpetual"
assert profile.venue == "BINANCE"
assert "BCHUSDT" in profile.instrument_id

print("auto instrument profile ok")
