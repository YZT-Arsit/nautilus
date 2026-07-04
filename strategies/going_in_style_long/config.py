"""Going in Style long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Going_in_Style_L`` system — the long mirror
of ``going_in_style_short``: a new-high-pullback long entry (``High >= Close[1] +
ATR[1]*Trigger`` after the prior bar made a new high) with a parabolic-SAR-style
accelerating trailing stop. Note the long defaults differ from the short
(Trigger=0.79, Acceleration=0.05, FirstBarMultp=5).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoingInStyleLongConfig:
    """User-facing parameters for the Going in Style long strategy."""

    length: int = 10             # Length: ATR / new-high lookback
    trigger: float = 0.79        # Trigger: ATR fraction above Close[1] for the entry price
    acceleration: float = 0.05   # Acceleration: parabolic AF step (capped at 0.2)
    first_bar_multp: float = 5.0  # FirstBarMultp: entry-bar stop = Low - StopATR*FirstBarMultp
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
