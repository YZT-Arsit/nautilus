"""RedRover long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``RedRover_L`` system — the long mirror of
``RedRover_S``. A support/resistance breakout on a weighted bar price: long an
upside break of the prior bar's resistance line, exit on an ATR profit target or
a reverse break below the prior support line.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedRoverLongConfig:
    """User-facing parameters for the RedRover long strategy."""

    atr_s: float = 3.0       # ATRs: ATR multiple for the profit target
    atr_length: int = 10     # ATRLength: ATR period
    tick: float = 0.01       # MinMove * PriceScale: one price tick (breakout buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.atr_length <= 0:
            raise ValueError("atr_length must be > 0.")
        if self.atr_s < 0:
            raise ValueError("atr_s must be >= 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
