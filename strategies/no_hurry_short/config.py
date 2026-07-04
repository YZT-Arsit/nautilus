"""No Hurry short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``NoHurrySystem_S`` system — a *shifted*
(time-delayed) high/low channel breakout with an ATR trailing stop. A break of
the lower channel value from ``chan_delay + 1`` bars ago opens the short; a rally
back through either the trailing ATR stop or the shifted upper channel covers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NoHurryShortConfig:
    """User-facing parameters for the No Hurry short strategy."""

    chan_length: int = 20     # ChanLength: Highest/Lowest lookback for the channel
    chan_delay: int = 15      # ChanDelay: bars to shift the channel back (uses [chan_delay+1])
    trailing_atrs: float = 3.0  # TrailingATRs: ATR multiple for the trailing stop
    atr_length: int = 10      # ATRLength: AvgTrueRange period
    tick: float = 0.01        # MinMove * PriceScale: one price tick (stop buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.chan_length <= 0:
            raise ValueError("chan_length must be > 0.")
        if self.chan_delay < 0:
            raise ValueError("chan_delay must be >= 0.")
        if self.atr_length <= 0:
            raise ValueError("atr_length must be > 0.")
        if self.trailing_atrs < 0:
            raise ValueError("trailing_atrs must be >= 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
