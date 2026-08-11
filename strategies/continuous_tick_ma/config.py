"""Configuration for the continuous event-time MA crossover."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContinuousTickMaConfig:
    fast_minutes: int = 5
    slow_minutes: int = 10

    def __post_init__(self) -> None:
        if self.fast_minutes <= 0:
            raise ValueError("fast_minutes must be positive")
        if self.slow_minutes <= self.fast_minutes:
            raise ValueError("slow_minutes must be greater than fast_minutes")

    @property
    def fast_name(self) -> str:
        return f"trade_price_mean_{self.fast_minutes}m"

    @property
    def slow_name(self) -> str:
        return f"trade_price_mean_{self.slow_minutes}m"
