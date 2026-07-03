"""TrendScore long strategy configuration (user-facing parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``TrendScore_L`` system — the long-side
mirror of ``TrendScore_S``. Scores the close against the prior ``look_back``
closes and goes long when both price and score sit ABOVE their moving averages,
with an ATR protective / trailing / break-even stop stack.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendScoreLongConfig:
    """User-facing parameters for the TrendScore long strategy."""

    look_back: int = 10                    # LookBack: closes to score the current close against
    ma_length: int = 18                    # MALength: MA period for close and score
    atr_length: int = 10                   # ATRLength: ATR period
    protect_stop_atr_multi: float = 0.5    # ProtectStopATRMulti: protective stop ATR multiple
    trail_stop_atr_multi: float = 3.0      # TrailStopATRMulti: trailing stop ATR multiple
    breakeven_stop_atr_multi: float = 5.0  # BreakEvenStopATRMulti: break-even trigger ATR multiple
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("look_back", "ma_length", "atr_length"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        for name in ("protect_stop_atr_multi", "trail_stop_atr_multi", "breakeven_stop_atr_multi"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0.")
