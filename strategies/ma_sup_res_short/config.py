"""MA Support/Resistance short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Moving_Average_Sup_and_Res_S`` system — a
variable support/resistance framework built from price vs a moving average. A
death-cross arms a support line at the bar's low (updated on every lower low
below the MA); a following golden-cross records that support line as the *short
entry line*; a later close back below that line opens a short, protected by an
ATR stop and trailed by a wider ATR stop.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaSupResShortConfig:
    """User-facing parameters for the MA Support/Resistance short strategy."""

    ma_length: int = 10                 # MALength: moving-average period (AverageFC of close)
    atr_length: int = 10                # ATRLength: AvgTrueRange period
    protect_stop_atr_multi: float = 0.5  # ProtectStopATRMulti: protective-stop ATR multiple
    trail_stop_atr_multi: float = 2.5    # TrailStopATRMulti: trailing-stop ATR multiple
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.ma_length <= 0:
            raise ValueError("ma_length must be > 0.")
        if self.atr_length <= 0:
            raise ValueError("atr_length must be > 0.")
        if self.protect_stop_atr_multi < 0:
            raise ValueError("protect_stop_atr_multi must be >= 0.")
        if self.trail_stop_atr_multi < 0:
            raise ValueError("trail_stop_atr_multi must be >= 0.")
