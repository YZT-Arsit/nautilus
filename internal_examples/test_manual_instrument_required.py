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


def expect_value_error(fn, expected_text):
    try:
        fn()
    except ValueError as exc:
        assert expected_text in str(exc)
        return
    raise AssertionError("Expected ValueError")


expect_value_error(
    lambda: AutoInstrumentProfileBuilder.build_profile(
        symbol="BCHUSDT",
        data_root=DATA_ROOT,
        venue="BINANCE",
    ),
    "instrument_type must be provided explicitly",
)

expect_value_error(
    lambda: AutoInstrumentProfileBuilder.build_profile(
        symbol="BCHUSDT",
        data_root=DATA_ROOT,
        instrument_type="crypto_perpetual",
    ),
    "venue must be provided explicitly",
)

legacy_profile = AutoInstrumentProfileBuilder.build_profile(
    symbol="BCHUSDT",
    data_root=DATA_ROOT,
    venue="BINANCE",
    require_explicit_type=False,
    allow_inference=True,
)
assert legacy_profile.instrument_type == "crypto_perpetual"

print("manual instrument required checks ok")
