#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import InstrumentProfile
from nautilus_ext.instruments import InstrumentRegistry


registry = InstrumentRegistry()
assert registry.list_profiles()
assert "BCHUSDT" in registry.list_symbols()

bch = registry.get("BCHUSDT", venue="BINANCE", instrument_type="crypto_perpetual")
assert bch.instrument_id == "BCHUSDT-PERP.BINANCE"

assert registry.find_all(instrument_type="crypto_perpetual")

dupes = InstrumentRegistry(load_defaults=False)
dupes.register(InstrumentProfile("DUP", "A", "equity", "DUP.A", "DUP"))
dupes.register(InstrumentProfile("DUP", "B", "equity", "DUP.B", "DUP"))
try:
    dupes.get("DUP")
except ValueError as exc:
    assert "Multiple profiles" in str(exc)
else:
    raise AssertionError("Expected duplicate symbol lookup to require venue")

print("instrument registry ok")
