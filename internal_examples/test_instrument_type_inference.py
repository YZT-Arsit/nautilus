#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import InstrumentTypeInferencer


BDB_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)

result = InstrumentTypeInferencer.infer_from_path_and_symbol(BDB_ROOT, "BCHUSDT")
assert result["instrument_type"] == "crypto_perpetual"
assert result["venue"] == "BINANCE"

result = InstrumentTypeInferencer.infer_from_path_and_symbol(None, "EUR/USD")
assert result["instrument_type"] == "currency_pair"

result = InstrumentTypeInferencer.infer_from_path_and_symbol(r"D:\Data\Equity", "AAPL")
assert result["instrument_type"] == "equity"

result = InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Data\Options",
    "AAPL240621C00190000",
    hints={"expiry": "2024-06-21", "strike_price": "190", "venue": "OPRA"},
)
assert result["instrument_type"] == "option_contract"

result = InstrumentTypeInferencer.infer_from_path_and_symbol(r"D:\Unknown", "MYSTERY")
assert result["instrument_type"] == "unknown"

print("instrument type inference ok")
