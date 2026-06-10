"""Compatibility wrapper — re-exports the canonical data layer.

The real implementation now lives in ``market_data_engine``. This module is kept
only so existing imports (`strategy_framework.data_loaders`) keep working; it
contains no logic of its own. Prefer importing from ``market_data_engine`` in
new code.
"""
from market_data_engine.loader import load_events
from market_data_engine.sources.csv_bars import load_csv_bars
from market_data_engine.sources.live_synthetic import load_live_synthetic
from market_data_engine.sources.synthetic import load_synthetic_bars

__all__ = [
    "load_events",
    "load_synthetic_bars",
    "load_csv_bars",
    "load_live_synthetic",
]
