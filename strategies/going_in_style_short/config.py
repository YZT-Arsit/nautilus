"""Going in Style short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Going_in_Style_S`` system — a
new-low-pullback short entry (``Low <= Close[1] - ATR[1]*Trigger`` after the prior
bar made a new low) with a parabolic-SAR-style accelerating trailing stop.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoingInStyleShortConfig:
    """User-facing parameters for the Going in Style short strategy."""

    length: int = 10             # Length: ATR / new-low lookback
    trigger: float = 0.5         # Trigger: ATR fraction below Close[1] for the entry price
    acceleration: float = 0.06   # Acceleration: parabolic AF step (capped at 0.2)
    first_bar_multp: float = 2.0  # FirstBarMultp: entry-bar stop = High + StopATR*FirstBarMultp
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("length must be > 0.")
        if self.trigger < 0:
            raise ValueError("trigger must be >= 0.")
        if self.acceleration <= 0:
            raise ValueError("acceleration must be > 0.")
        if self.first_bar_multp < 0:
            raise ValueError("first_bar_multp must be >= 0.")
