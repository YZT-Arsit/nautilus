"""Live synthetic bar source — a dependency-free streaming skeleton.

``stream()`` returns a generator (a stand-in for a real feed). There is no
network or exchange dependency; real feeds should implement
``data_engine.streams.base.EventSource`` instead.
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Iterator

from data_engine.adapters.bar_adapter import make_bar_event, make_bars
from data_engine.events import BarEvent
from data_engine.sources.synthetic import demo_closes
from data_engine.time import ONE_SECOND_NS


class LiveSyntheticBarSource:
    """Warmup is a list; the live stream is a generator (optionally delayed)."""

    def __init__(
        self,
        instrument_id: str = "BTC/USDT",
        warmup_bars: int = 20,
        live_bars: int = 20,
        delay_seconds: float = 0.0,
    ) -> None:
        self._instrument_id = instrument_id
        self._warmup_bars = warmup_bars
        self._live_bars = live_bars
        self._delay_seconds = delay_seconds

    def warmup(self) -> list[BarEvent]:
        warmup_closes, _ = demo_closes(self._warmup_bars, self._live_bars)
        return make_bars(warmup_closes, instrument_id=self._instrument_id)

    def stream(self) -> Iterator[BarEvent]:
        _, live_closes = demo_closes(self._warmup_bars, self._live_bars)
        start_ns = self._warmup_bars * ONE_SECOND_NS
        for i, close in enumerate(live_closes):
            if self._delay_seconds > 0:
                time.sleep(self._delay_seconds)
            yield make_bar_event(
                close=close,
                instrument_id=self._instrument_id,
                event_time_ns=start_ns + i * ONE_SECOND_NS,
            )


def load_live_synthetic(data_config: dict[str, Any]) -> tuple[list[BarEvent], Iterable[BarEvent]]:
    """Build a LiveSyntheticBarSource and return ``(warmup_list, live_generator)``."""
    source = LiveSyntheticBarSource(
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        warmup_bars=int(data_config.get("warmup_bars", 20)),
        live_bars=int(data_config.get("live_bars", 20)),
        delay_seconds=float(data_config.get("delay_seconds", 0.0)),
    )
    return source.warmup(), source.stream()
