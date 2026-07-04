"""Swinger long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Swinger_L`` system — the long mirror of
``Swinger_S``. A long-period trend MA judges the trend, and a price oscillator
(fast MA minus slow MA) measures the momentum of the moving averages; long when
price is above the trend MA while the still-negative momentum is strengthening,
sell when momentum turns back down and price breaks the recent N-bar low.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwingerLongConfig:
    """User-facing parameters for the Swinger long strategy."""

    fast_ma_length: int = 5     # FastMALength: fast MA in the price oscillator
    slow_ma_length: int = 20    # SlowMALength: slow MA in the price oscillator
    trend_ma_length: int = 50   # TrendMALength: long-period trend MA
    exit_stop_n: int = 3        # ExitStopN: bars for the lowest-low exit trigger
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("fast_ma_length", "slow_ma_length", "trend_ma_length", "exit_stop_n"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
