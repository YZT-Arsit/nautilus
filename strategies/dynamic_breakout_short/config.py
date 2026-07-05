"""Dynamic Breakout II short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``DynamicBreakOutII_S`` system — an adaptive
Bollinger + Donchian breakout whose lookback window self-adjusts to the change in
30-bar volatility (clamped to ``[floor_amt, ceiling_amt]``). Short-only: a bar
closing under the (previous) lower Bollinger band with a break of the (previous)
Donchian lower shorts; the position covers on an upper breakout or a cross back
above the adaptive mid-line.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DynamicBreakoutShortConfig:
    """User-facing parameters for the Dynamic Breakout II short strategy."""

    ceiling_amt: int = 60        # ceilingAmt: adaptive-lookback upper clamp
    floor_amt: int = 20          # floorAmt: adaptive-lookback lower clamp
    bol_band_trig: float = 2.0   # bolBandTrig: Bollinger-band standard-deviation multiple
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.floor_amt <= 0:
            raise ValueError("floor_amt must be > 0.")
        if self.ceiling_amt < self.floor_amt:
            raise ValueError("ceiling_amt must be >= floor_amt.")
        if self.bol_band_trig < 0:
            raise ValueError("bol_band_trig must be >= 0.")
