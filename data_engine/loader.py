"""Canonical data-loading entry point.

``load_events`` reads a config's ``data:`` section and returns
``(warmup_events, live_events)`` for the configured ``mode``. This is the true
implementation; ``strategy_framework/data_loaders.py`` is only a thin wrapper.
"""
from __future__ import annotations

from typing import Any, Iterable

from data_engine.sources.binance_live import load_binance_ws
from data_engine.sources.csv_bars import load_csv_bars
from data_engine.sources.live_gateway import load_live_gateway
from data_engine.sources.live_synthetic import load_live_synthetic
from data_engine.sources.parquet_bars import load_parquet_bars
from data_engine.sources.parquet_trades import load_parquet_trades
from data_engine.sources.parquet_funding import load_parquet_funding
from data_engine.sources.synthetic import load_synthetic_bars
from data_engine.sources.synthetic_trades import load_synthetic_trades

# mode -> loader. Register new sources here.
_LOADERS = {
    "synthetic": load_synthetic_bars,
    "csv_bars": load_csv_bars,
    "hive_parquet_bars": load_parquet_bars,
    "live_synthetic": load_live_synthetic,
    "live_gateway": load_live_gateway,
    "binance_ws": load_binance_ws,  # live Binance public market-data WS (trades+quotes)
    # Trade (tick) sources — produce TradeEvent, not BarEvent.
    "synthetic_trades": load_synthetic_trades,
    "hive_parquet_trades": load_parquet_trades,
    "hive_parquet_funding": load_parquet_funding,
}


def load_events(data_config: dict[str, Any]) -> tuple[list[Any], Iterable[Any]]:
    """Return ``(warmup_events, live_events)`` for the configured data mode."""
    mode = data_config.get("mode", "synthetic")
    loader = _LOADERS.get(mode)
    if loader is None:
        valid = ", ".join(sorted(_LOADERS))
        raise ValueError(f"unsupported data mode {mode!r}. Supported modes: {valid}")
    return loader(data_config)
