from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from nautilus_ext.strategies.interfaces.input_types import MarketEvent
from nautilus_ext.strategies.interfaces.output_types import SignalResult
from nautilus_ext.strategies.interfaces.strategy_schema import StrategyInputSchema


class BaseSignalEngine(Protocol):
    name: str
    input_schema: StrategyInputSchema

    def reset(self) -> None:
        ...

    def warmup(self, events: Iterable[MarketEvent]) -> None:
        ...

    def update(self, event: MarketEvent, context: dict | None = None) -> SignalResult:
        ...

    def state_dict(self) -> dict:
        ...

    def load_state_dict(self, state: dict) -> None:
        ...
