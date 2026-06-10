"""Data source selection for the shared strategy runner.

``load_events`` reads the ``data:`` section of a strategy config and returns
``(warmup_events, live_events)``. Only synthetic data is supported for now; new
sources (files, catalogs, live feeds) plug in here without touching
``run_strategy.py``.
"""
from __future__ import annotations

from typing import Any

from nautilus_ext.features.examples.synthetic_bars import ONE_SECOND_NS, BarEvent, make_bars


def load_synthetic_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Generic flat -> rise -> fall price path that exercises crossovers.

    Recognised keys: ``instrument_id``, ``warmup_bars``, ``live_bars``.
    """
    instrument = data_config.get("instrument_id", "BTC/USDT")
    warmup_n = int(data_config.get("warmup_bars", 20))
    live_n = int(data_config.get("live_bars", 20))

    warmup_closes = [100.0] * warmup_n
    live_closes = ([100.0] + [110.0] * 3 + [100.0] * 3 + [90.0] * 3 + [80.0] * live_n)[:live_n]

    warmup_bars = make_bars(warmup_closes, instrument_id=instrument)
    live_bars = make_bars(live_closes, instrument_id=instrument, start_ns=len(warmup_bars) * ONE_SECOND_NS)
    return warmup_bars, live_bars


# mode -> loader. Add new sources here.
_LOADERS = {
    "synthetic": load_synthetic_bars,
}


def load_events(data_config: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """Return ``(warmup_events, live_events)`` for the configured data mode."""
    mode = data_config.get("mode", "synthetic")
    loader = _LOADERS.get(mode)
    if loader is None:
        valid = ", ".join(sorted(_LOADERS))
        raise ValueError(f"unsupported data mode {mode!r}. Supported modes: {valid}")
    return loader(data_config)
