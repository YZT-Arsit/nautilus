"""market_data_engine — the formal data processing layer.

Our own design; it does **not** depend on Nautilus Trader's native data system.
Owns event dataclasses, timestamp/validation helpers, bar adapters, and data
sources (synthetic, CSV historical, live synthetic skeleton).

Public API::

    from market_data_engine import BarEvent, load_events, make_bar_event, make_bars
"""
from market_data_engine.adapters.bar_adapter import make_bar_event, make_bars
from market_data_engine.events import BarEvent
from market_data_engine.loader import load_events

__all__ = [
    "BarEvent",
    "load_events",
    "make_bar_event",
    "make_bars",
]
