"""Canonical data-loading entry point.

``load_events`` reads a config's ``data:`` section and returns
``(warmup_events, live_events)`` for the configured ``mode``. This is the true
implementation; ``strategy_framework/data_loaders.py`` is only a thin wrapper.
"""
from __future__ import annotations

from typing import Any, Iterable

from market_data_engine.sources.csv_bars import load_csv_bars
from market_data_engine.sources.live_synthetic import load_live_synthetic
from market_data_engine.sources.synthetic import load_synthetic_bars

# mode -> loader. Register new sources here.
_LOADERS = {
    "synthetic": load_synthetic_bars,
    "csv_bars": load_csv_bars,
    "live_synthetic": load_live_synthetic,
}


def load_events(data_config: dict[str, Any]) -> tuple[list[Any], Iterable[Any]]:
    """Return ``(warmup_events, live_events)`` for the configured data mode."""
    mode = data_config.get("mode", "synthetic")
    loader = _LOADERS.get(mode)
    if loader is None:
        valid = ", ".join(sorted(_LOADERS))
        raise ValueError(f"unsupported data mode {mode!r}. Supported modes: {valid}")
    return loader(data_config)
