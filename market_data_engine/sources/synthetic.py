"""Synthetic bar source — a generated flat -> rise -> fall demo path."""
from __future__ import annotations

from typing import Any

from market_data_engine.adapters.bar_adapter import make_bars
from market_data_engine.events import BarEvent
from market_data_engine.time import ONE_SECOND_NS


def demo_closes(warmup_bars: int, live_bars: int) -> tuple[list[float], list[float]]:
    """Flat warmup, then a rise -> fall path that triggers an MA crossover."""
    warmup_closes = [100.0] * warmup_bars
    live_closes = ([100.0] + [110.0] * 3 + [100.0] * 3 + [90.0] * 3 + [80.0] * live_bars)[:live_bars]
    return warmup_closes, live_closes


class SyntheticBarSource:
    """Generates deterministic bars; both warmup and stream are lists."""

    def __init__(
        self,
        instrument_id: str = "BTC/USDT",
        warmup_bars: int = 20,
        live_bars: int = 20,
    ) -> None:
        self._instrument_id = instrument_id
        self._warmup_bars = warmup_bars
        self._live_bars = live_bars

    def warmup(self) -> list[BarEvent]:
        warmup_closes, _ = demo_closes(self._warmup_bars, self._live_bars)
        return make_bars(warmup_closes, instrument_id=self._instrument_id)

    def stream(self) -> list[BarEvent]:
        _, live_closes = demo_closes(self._warmup_bars, self._live_bars)
        start_ns = self._warmup_bars * ONE_SECOND_NS
        return make_bars(live_closes, instrument_id=self._instrument_id, start_ns=start_ns)


def load_synthetic_bars(data_config: dict[str, Any]) -> tuple[list[BarEvent], list[BarEvent]]:
    """Build a SyntheticBarSource from a config and return ``(warmup, live)``."""
    source = SyntheticBarSource(
        instrument_id=data_config.get("instrument_id", "BTC/USDT"),
        warmup_bars=int(data_config.get("warmup_bars", 20)),
        live_bars=int(data_config.get("live_bars", 20)),
    )
    return source.warmup(), source.stream()
