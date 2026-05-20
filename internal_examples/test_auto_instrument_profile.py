#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import AutoInstrumentProfileBuilder


print("This test exercises optional inference mode, not the production default path.")

DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)

profile = AutoInstrumentProfileBuilder.build_profile(
    symbol="BCHUSDT",
    data_root=DATA_ROOT,
    venue="BINANCE",
    require_explicit_type=False,
    allow_inference=True,
)

print(f"instrument_type: {profile.instrument_type}")
print(f"venue: {profile.venue}")
print(f"instrument_id: {profile.instrument_id}")
print(f"source: {profile.source}")

assert profile.instrument_type == "crypto_perpetual"
assert profile.venue == "BINANCE"
assert "BCHUSDT" in profile.instrument_id

print("legacy auto instrument profile ok")
