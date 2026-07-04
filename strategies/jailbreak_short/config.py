"""JailBreak short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``JailBreakSys_S`` system — a price-range
breakout: a break below the long-period (``max(Length1, Length2)``) low channel
opens a short, exited by an ATR protective stop or a break above the short-period
(``min(Length1, Length2)``) high channel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JailBreakShortConfig:
    """User-facing parameters for the JailBreak short strategy."""

    length1: int = 50        # Length1: one range period (entry uses max(length1, length2))
    length2: int = 30        # Length2: other range period (exit uses min(length1, length2))
    ips: float = 4.0         # IPS: protective-stop ATR multiple
    atr_val: int = 10        # AtrVal: AvgTrueRange period
    tick: float = 0.01       # MinMove * PriceScale: one price tick (breakout buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.length1 <= 0 or self.length2 <= 0:
            raise ValueError("length1 and length2 must be > 0.")
        if self.atr_val <= 0:
            raise ValueError("atr_val must be > 0.")
        if self.ips < 0:
            raise ValueError("ips must be >= 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
