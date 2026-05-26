from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from nautilus_ext.data.events import BarEvent
from nautilus_ext.data.events import QuoteTickEvent


@dataclass(frozen=True)
class BarAggregationConfig:
    interval: str = "1min"
    price_mode: str = "mid"
    volume_mode: str = "tick_count"

    def __post_init__(self) -> None:
        if self.price_mode != "mid":
            raise NotImplementedError("Only price_mode='mid' is currently supported.")
        if self.volume_mode != "tick_count":
            raise NotImplementedError("Only volume_mode='tick_count' is currently supported.")
        try:
            pd.Timestamp("2020-01-01", tz="UTC").floor(self.interval)
        except ValueError as exc:
            raise ValueError(f"Invalid bar interval: {self.interval!r}.") from exc


class TickToBarAggregator:
    def __init__(self, config: BarAggregationConfig | None = None) -> None:
        self.config = config or BarAggregationConfig()
        self.reset()

    def reset(self) -> None:
        self._instrument_id: str | None = None
        self._window: datetime | None = None
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._volume = 0.0
        self._last_ts: datetime | None = None

    def update(self, event: QuoteTickEvent) -> BarEvent | None:
        if self._last_ts is not None and event.ts_event < self._last_ts:
            raise ValueError("QuoteTick events must be ordered by ts_event.")
        if self._instrument_id is not None and event.instrument_id != self._instrument_id:
            raise ValueError("TickToBarAggregator handles one instrument per instance.")

        window = pd.Timestamp(event.ts_event).floor(self.config.interval).to_pydatetime()
        emitted = None
        if self._window is not None and window != self._window:
            emitted = self._build_bar()
            self._start_bar(event, window)
        elif self._window is None:
            self._start_bar(event, window)
        else:
            self._update_bar(event)
        self._last_ts = event.ts_event
        return emitted

    def flush(self) -> BarEvent | None:
        if self._window is None:
            return None
        bar = self._build_bar()
        self._window = None
        return bar

    def state_dict(self) -> dict:
        return {
            "config": {
                "interval": self.config.interval,
                "price_mode": self.config.price_mode,
                "volume_mode": self.config.volume_mode,
            },
            "instrument_id": self._instrument_id,
            "window": self._window.isoformat() if self._window is not None else None,
            "open": self._open,
            "high": self._high,
            "low": self._low,
            "close": self._close,
            "volume": self._volume,
            "last_ts": self._last_ts.isoformat() if self._last_ts is not None else None,
        }

    def load_state_dict(self, state: dict) -> None:
        expected = {
            "interval": self.config.interval,
            "price_mode": self.config.price_mode,
            "volume_mode": self.config.volume_mode,
        }
        if state.get("config") != expected:
            raise ValueError("Bar aggregation config does not match checkpoint.")
        self._instrument_id = state.get("instrument_id")
        self._window = _from_iso(state.get("window"))
        self._open = state.get("open")
        self._high = state.get("high")
        self._low = state.get("low")
        self._close = state.get("close")
        self._volume = float(state.get("volume", 0.0))
        self._last_ts = _from_iso(state.get("last_ts"))

    def _start_bar(self, event: QuoteTickEvent, window: datetime) -> None:
        price = event.mid_price
        self._instrument_id = event.instrument_id
        self._window = window
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume = 1.0

    def _update_bar(self, event: QuoteTickEvent) -> None:
        price = event.mid_price
        self._high = max(self._high, price)  # type: ignore[arg-type]
        self._low = min(self._low, price)  # type: ignore[arg-type]
        self._close = price
        self._volume += 1.0

    def _build_bar(self) -> BarEvent:
        return BarEvent(
            instrument_id=self._instrument_id or "",
            open=self._open or 0.0,
            high=self._high or 0.0,
            low=self._low or 0.0,
            close=self._close or 0.0,
            volume=self._volume,
            ts_event=self._window,  # type: ignore[arg-type]
            source="quote_tick_mid",
            volume_type="synthetic_tick_count",
        )


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None
