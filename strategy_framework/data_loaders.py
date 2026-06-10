"""Data source selection for the shared strategy runner.

``load_events`` reads the ``data:`` section of a strategy config and returns
``(warmup_events, live_events)``. Three modes are supported:

* ``synthetic``      — generated flat -> rise -> fall demo path (default).
* ``csv_bars``       — historical replay from a local CSV (stdlib ``csv`` only).
* ``live_synthetic`` — streaming skeleton; live events are a generator.

New sources (real feeds, catalogs) register in ``_LOADERS`` without touching
``run_strategy.py``.
"""
from __future__ import annotations

import csv
import time
from typing import Any, Iterator

from nautilus_ext.features.examples.synthetic_bars import ONE_SECOND_NS, BarEvent, make_bars

# Timestamp unit -> nanoseconds multiplier.
_TIMESTAMP_UNITS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}


def _demo_closes(warmup_n: int, live_n: int) -> tuple[list[float], list[float]]:
    """Shared flat -> rise -> fall close path used by the synthetic modes."""
    warmup_closes = [100.0] * warmup_n
    live_closes = ([100.0] + [110.0] * 3 + [100.0] * 3 + [90.0] * 3 + [80.0] * live_n)[:live_n]
    return warmup_closes, live_closes


def load_synthetic_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Generic flat -> rise -> fall price path that exercises crossovers.

    Recognised keys: ``instrument_id``, ``warmup_bars``, ``live_bars``.
    """
    instrument = data_config.get("instrument_id", "BTC/USDT")
    warmup_n = int(data_config.get("warmup_bars", 20))
    live_n = int(data_config.get("live_bars", 20))

    warmup_closes, live_closes = _demo_closes(warmup_n, live_n)
    warmup_bars = make_bars(warmup_closes, instrument_id=instrument)
    live_bars = make_bars(live_closes, instrument_id=instrument, start_ns=len(warmup_bars) * ONE_SECOND_NS)
    return warmup_bars, live_bars


# ---------------------------------------------------------------------------
# csv_bars — historical backtest-style replay
# ---------------------------------------------------------------------------

def _to_float(value: str, column: str, row_index: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"row {row_index}: column {column!r} is not numeric: {value!r}") from None


def _row_to_bar(row: dict[str, str], index: int, cfg: dict[str, Any], instrument: str) -> BarEvent:
    close_col = cfg.get("close_column", "close")
    if close_col not in row or row[close_col] in (None, ""):
        raise ValueError(f"row {index}: required close column {close_col!r} is missing")
    close = _to_float(row[close_col], close_col, index)

    def _field(key: str, default: float) -> float:
        col = cfg.get(key)
        if col and row.get(col) not in (None, ""):
            return _to_float(row[col], col, index)
        return default

    # Defaults derived from close when O/H/L/V columns are absent.
    open_ = _field("open_column", close)
    high = _field("high_column", close)
    low = _field("low_column", close)
    volume = _field("volume_column", 0.0)

    ts_col = cfg.get("timestamp_column")
    if ts_col and row.get(ts_col) not in (None, ""):
        unit = cfg.get("timestamp_unit", "ns")
        if unit not in _TIMESTAMP_UNITS:
            valid = ", ".join(_TIMESTAMP_UNITS)
            raise ValueError(f"unsupported timestamp_unit {unit!r}. Supported units: {valid}")
        event_time_ns = int(_to_float(row[ts_col], ts_col, index) * _TIMESTAMP_UNITS[unit])
    else:
        event_time_ns = index * ONE_SECOND_NS  # monotonic fallback

    return BarEvent(
        close=close, open=open_, high=high, low=low, volume=volume,
        instrument_id=instrument, event_time_ns=event_time_ns,
    )


def load_csv_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Replay bars from a local CSV file (stdlib ``csv`` only, no pandas).

    The whole file is one series; the first ``warmup_bars`` rows (after sorting
    by event time) become the warmup set and the remainder the live set.
    """
    path = data_config.get("path")
    if not path:
        raise ValueError("csv_bars mode requires a 'path' to the CSV file")
    instrument = data_config.get("instrument_id", "BTC/USDT")
    warmup_n = int(data_config.get("warmup_bars", 20))

    # Validate unsupported unit up front, even if the column is later absent.
    unit = data_config.get("timestamp_unit", "ns")
    if unit not in _TIMESTAMP_UNITS:
        valid = ", ".join(_TIMESTAMP_UNITS)
        raise ValueError(f"unsupported timestamp_unit {unit!r}. Supported units: {valid}")

    with open(path, newline="") as fh:
        bars = [_row_to_bar(row, i, data_config, instrument) for i, row in enumerate(csv.DictReader(fh))]

    # Sort by event time once, here — never inside the engine's on_event().
    bars.sort(key=lambda b: b.event_time_ns)
    return bars[:warmup_n], bars[warmup_n:]


# ---------------------------------------------------------------------------
# live_synthetic — streaming skeleton (no external dependencies)
# ---------------------------------------------------------------------------

def load_live_synthetic(data_config: dict[str, Any]) -> tuple[list[BarEvent], Iterator[BarEvent]]:
    """Warmup as a list; live events as a generator (a stand-in for a real feed)."""
    instrument = data_config.get("instrument_id", "BTC/USDT")
    warmup_n = int(data_config.get("warmup_bars", 20))
    live_n = int(data_config.get("live_bars", 20))
    delay_seconds = float(data_config.get("delay_seconds", 0.0))

    warmup_closes, live_closes = _demo_closes(warmup_n, live_n)
    warmup_bars = make_bars(warmup_closes, instrument_id=instrument)
    start_ns = len(warmup_bars) * ONE_SECOND_NS

    def _stream() -> Iterator[BarEvent]:
        for i, close in enumerate(live_closes):
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            yield BarEvent(
                close=close, open=close, high=close, low=close, volume=0.0,
                instrument_id=instrument, event_time_ns=start_ns + i * ONE_SECOND_NS,
            )

    return warmup_bars, _stream()


# mode -> loader. Add new sources here.
_LOADERS = {
    "synthetic": load_synthetic_bars,
    "csv_bars": load_csv_bars,
    "live_synthetic": load_live_synthetic,
}


def load_events(data_config: dict[str, Any]) -> tuple[list[Any], Any]:
    """Return ``(warmup_events, live_events)`` for the configured data mode."""
    mode = data_config.get("mode", "synthetic")
    loader = _LOADERS.get(mode)
    if loader is None:
        valid = ", ".join(sorted(_LOADERS))
        raise ValueError(f"unsupported data mode {mode!r}. Supported modes: {valid}")
    return loader(data_config)
