#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import InstrumentTypeInferencer


DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)

assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    DATA_ROOT,
    "BCHUSDT",
)["instrument_type"] == "crypto_perpetual"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(None, "EUR/USD")[
    "instrument_type"
] == "currency_pair"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Data\Equity",
    "AAPL",
    hints={"venue": "XNAS"},
)["instrument_type"] == "equity"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Data\Futures",
    "ESM4",
    hints={"venue": "XCME"},
)["instrument_type"] == "futures_contract"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Data\Options",
    "AAPL250117C00200000",
    hints={"expiry": "20250117", "strike_price": "200", "option_kind": "CALL"},
)["instrument_type"] == "option_contract"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Data\Commodity",
    "XAUUSD",
)["instrument_type"] == "commodity"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Data\CFD",
    "US500",
)["instrument_type"] == "cfd"
assert InstrumentTypeInferencer.infer_from_path_and_symbol(
    r"D:\Unknown",
    "MYSTERY",
)["instrument_type"] == "unknown"

print("instrument type inference ok")
