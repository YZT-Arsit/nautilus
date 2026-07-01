"""Verify data_engine/feature_engine are Nautilus-free and binance_ws is wired.

1. Import data_engine + feature_engine, then assert no ``nautilus_trader`` module
   got loaded as a side effect (framework-agnostic contract).
2. Assert ``binance_ws`` is a registered loader mode.
3. Drive the ``binance_ws`` mode through the canonical ``load_events`` and pull a
   few live events (proves the loader path connects + normalizes).
"""
import sys

import data_engine  # noqa: F401
import feature_engine  # noqa: F401
from data_engine import loader
from data_engine.loader import load_events

leaked = sorted(m for m in sys.modules if m == "nautilus_trader" or m.startswith("nautilus_trader."))
print(f"[decouple] nautilus_trader modules loaded by data/feature import: {leaked or 'NONE'}")
assert not leaked, f"data_engine/feature_engine leaked Nautilus imports: {leaked}"

assert "binance_ws" in loader._LOADERS, "binance_ws not registered"
print(f"[decouple] loader modes: {sorted(loader._LOADERS)}")

cfg = {
    "mode": "binance_ws",
    "symbol": "btcusdt",
    "streams": "aggTrade,bookTicker",
    "base_url": "wss://data-stream.binance.vision:9443",
    "instrument_id": "BTCUSDT.BINANCE",
    "max_messages": 6,
    "timeout_seconds": 20,
}
warmup, live = load_events(cfg)
print(f"[binance_ws] warmup={len(warmup)} (expected 0); draining live...")
n = 0
first = None
for ev in live:
    n += 1
    if first is None:
        first = ev
if first is not None:
    print(f"[binance_ws] first live event: type={first.event_type} "
          f"instrument={first.instrument_id} event_time_ns={first.event_time_ns}")
print(f"[binance_ws] drained {n} live event(s) via load_events -> "
      f"{'OK' if n else 'NO DATA'}")
