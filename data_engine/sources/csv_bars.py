"""CSV historical bar source (stdlib ``csv`` only, no pandas)."""
from __future__ import annotations

import csv
from typing import Any

from data_engine.adapters.bar_adapter import make_bar_event
from data_engine.events import BarEvent
from data_engine.split import split_warmup_live
from data_engine.time import ONE_SECOND_NS, to_event_time_ns, validate_time_unit
from data_engine.validation import optional_numeric, require_numeric


class CsvBarSource:
    """Loads bars from a CSV file, splitting the first ``warmup_bars`` rows off.

    Rows are sorted by event time once after reading — never inside the engine's
    ``on_event()``.
    """

    def __init__(
        self,
        path: str,
        instrument_id: str,
        warmup_bars: int = 0,
        timestamp_column: str | None = "event_time_ns",
        timestamp_unit: str = "ns",
        close_column: str = "close",
        open_column: str | None = "open",
        high_column: str | None = "high",
        low_column: str | None = "low",
        volume_column: str | None = "volume",
    ) -> None:
        validate_time_unit(timestamp_unit)  # fail fast, even if column is absent
        self._path = path
        self._instrument_id = instrument_id
        self._warmup_bars = warmup_bars
        self._timestamp_column = timestamp_column
        self._timestamp_unit = timestamp_unit
        self._close_column = close_column
        self._open_column = open_column
        self._high_column = high_column
        self._low_column = low_column
        self._volume_column = volume_column
        self._bars: list[BarEvent] | None = None  # cache: read the file once

    def _row_to_bar(self, row: dict[str, str], index: int) -> BarEvent:
        close_col = self._close_column
        if close_col not in row or row.get(close_col) in (None, ""):
            raise ValueError(f"row {index}: required close column {close_col!r} is missing")
        close = require_numeric(row[close_col], close_col, index)

        def _opt(col: str | None, default: float) -> float:
            if col and row.get(col) not in (None, ""):
                return optional_numeric(row[col], default, col, index)
            return default

        ts_col = self._timestamp_column
        if ts_col and row.get(ts_col) not in (None, ""):
            try:
                event_time_ns = to_event_time_ns(row[ts_col], self._timestamp_unit)
            except ValueError as exc:
                raise ValueError(f"row {index}: {exc}") from None
        else:
            event_time_ns = index * ONE_SECOND_NS  # monotonic fallback

        return make_bar_event(
            close=close,
            open=_opt(self._open_column, close),
            high=_opt(self._high_column, close),
            low=_opt(self._low_column, close),
            volume=_opt(self._volume_column, 0.0),
            instrument_id=self._instrument_id,
            event_time_ns=event_time_ns,
        )

    def _load_sorted(self) -> list[BarEvent]:
        with open(self._path, newline="", encoding="utf-8") as fh:
            bars = [self._row_to_bar(row, i) for i, row in enumerate(csv.DictReader(fh))]
        bars.sort(key=lambda b: b.event_time_ns)
        return bars

    def _bars_cached(self) -> list[BarEvent]:
        if self._bars is None:
            self._bars = self._load_sorted()
        return self._bars

    def warmup(self) -> list[BarEvent]:
        return split_warmup_live(self._bars_cached(), self._warmup_bars)[0]

    def stream(self) -> list[BarEvent]:
        return split_warmup_live(self._bars_cached(), self._warmup_bars)[1]


def load_csv_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Build a CsvBarSource from a config and return ``(warmup, live)``."""
    path = data_config.get("path")
    if not path:
        raise ValueError("csv_bars mode requires a 'path' to the CSV file")
    source = CsvBarSource(
        path=path,
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        warmup_bars=int(data_config.get("warmup_bars", 0)),
        timestamp_column=data_config.get("timestamp_column", "event_time_ns"),
        timestamp_unit=data_config.get("timestamp_unit", "ns"),
        close_column=data_config.get("close_column", "close"),
        open_column=data_config.get("open_column", "open"),
        high_column=data_config.get("high_column", "high"),
        low_column=data_config.get("low_column", "low"),
        volume_column=data_config.get("volume_column", "volume"),
    )
    return split_warmup_live(source._bars_cached(), source._warmup_bars)
