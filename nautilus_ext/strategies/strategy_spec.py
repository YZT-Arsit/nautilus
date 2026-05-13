from dataclasses import dataclass
from typing import Any
from typing import Callable


@dataclass(frozen=True)
class StrategyContext:
    bar_type: object
    instrument: object
    strategy_name: str
    run_id: str
    params: dict[str, Any]


@dataclass(frozen=True)
class NautilusStrategySpec:
    name: str
    factory: Callable[[StrategyContext], object]
    params: dict[str, Any] | None = None
    enabled: bool = True

    def __post_init__(self):
        if not self.name:
            raise ValueError("Strategy spec name must be non-empty.")
        if self.params is None:
            object.__setattr__(self, "params", {})

    def build_strategy(self, context: StrategyContext):
        if not self.enabled:
            raise ValueError(f"Strategy spec {self.name!r} is disabled.")

        strategy = self.factory(context)
        if strategy is None:
            raise ValueError(f"Strategy factory for {self.name!r} returned None.")

        return strategy
