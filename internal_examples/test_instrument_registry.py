#!/usr/bin/env python3
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.instruments import InstrumentProfile
from nautilus_ext.instruments import InstrumentRegistry


registry = InstrumentRegistry()
profiles = registry.list_profiles()
assert profiles

bch = registry.get("BCHUSDT", venue="BINANCE", instrument_type="crypto_perpetual")
assert bch.instrument_id == "BCHUSDT-PERP.BINANCE"

symbols = registry.list_symbols()
assert "BCHUSDT" in symbols

crypto_perps = registry.find_all(instrument_type="crypto_perpetual")
assert any(profile.symbol == "BCHUSDT" for profile in crypto_perps)

duplicate_registry = InstrumentRegistry(load_defaults=False)
duplicate_registry.register(
    InstrumentProfile("DUP", "VENUE1", "equity", "DUP.VENUE1", "DUP")
)
duplicate_registry.register(
    InstrumentProfile("DUP", "VENUE2", "equity", "DUP.VENUE2", "DUP")
)
try:
    duplicate_registry.get("DUP")
except ValueError as exc:
    assert "Multiple profiles" in str(exc)
else:
    raise AssertionError("Expected duplicate symbol lookup to require venue")

print("instrument registry ok")
