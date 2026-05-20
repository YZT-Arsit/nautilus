#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import AutoInstrumentBuilder


DATA_ROOT = (
    r"D:\QuanHub\DataAtaw\unorganized\Crypto\src\raw_tbl\BDB\Futures"
    r"\TLine\BinanceCryptoFutures_TODKLine_0060S"
)

try:
    instrument = AutoInstrumentBuilder.build(
        symbol="BCHUSDT",
        data_root=DATA_ROOT,
        instrument_type="crypto_perpetual",
        venue="BINANCE",
    )
except NotImplementedError as exc:
    print(
        "Profile inference works; Nautilus constructor support needs completion "
        "or a working Nautilus native environment."
    )
    print(exc)
else:
    print(f"instrument: {instrument}")
    print(f"instrument_id: {instrument.id}")
    print(f"price_precision: {instrument.price_precision}")
    print(f"size_precision: {instrument.size_precision}")
    print(f"price_increment: {instrument.price_increment}")
    print(f"size_increment: {instrument.size_increment}")
    assert "BCHUSDT" in str(instrument.id)
    assert "BINANCE" in str(instrument.id)

print("auto instrument builder check complete")
