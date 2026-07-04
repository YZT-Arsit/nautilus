"""Swinger short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Swinger_S`` system — a long-period trend
MA judges the trend, and a price oscillator (fast MA minus slow MA) measures the
momentum of the moving averages; short when price is below the trend MA while the
still-positive momentum is weakening, cover when momentum turns back up and price
breaks the recent N-bar high.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwingerShortConfig:
    """User-facing parameters for the Swinger short strategy."""

    fast_ma_length: int = 5     # FastMALength: fast MA in the price oscillator
    slow_ma_length: int = 20    # SlowMALength: slow MA in the price oscillator
    trend_ma_length: int = 50   # TrendMALength: long-period trend MA
    exit_stop_n: int = 3        # ExitStopN: bars for the highest-high exit trigger
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("fast_ma_length", "slow_ma_length", "trend_ma_length", "exit_stop_n"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
