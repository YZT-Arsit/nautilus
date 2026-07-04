"""In The Zone long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``In_The_Zone_L`` system — the long mirror of
``in_the_zone_short``: a 4-bar box breakout. An up-move (bar-1 close above bar-3
high) arms a long zone (upper = bar-3 high, lower = lowest low of the prior
CancelFlagN bars); if the current close sits inside the zone the setup succeeds
with the trigger at the bar's high. A break above the trigger opens the long,
exited by an ATR protective stop, an ATR break-even stop, or an ATR profit target.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InTheZoneLongConfig:
    """User-facing parameters for the In The Zone long strategy."""

    atr_length: int = 10                    # ATRLength: AvgTrueRange period
    cancel_flag_n: int = 5                  # CancelFlagN: lowest-low lookback for the zone lower rail
    protect_stop_atr_multi: float = 0.5     # ProtectStopATRMulti: protective-stop ATR multiple
    break_even_stop_atr_multi: float = 3.0  # BreakEvenStopATRMulti: break-even-arm ATR multiple
    profit_target_atr_multi: float = 5.0    # ProfitTargetATRMulti: profit-target ATR multiple
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.atr_length <= 0:
            raise ValueError("atr_length must be > 0.")
        if self.cancel_flag_n <= 0:
            raise ValueError("cancel_flag_n must be > 0.")
        for name in ("protect_stop_atr_multi", "break_even_stop_atr_multi", "profit_target_atr_multi"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0.")
