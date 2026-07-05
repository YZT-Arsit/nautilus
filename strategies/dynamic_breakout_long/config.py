"""Dynamic Breakout II long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``DynamicBreakOutII_L`` system — an adaptive
Bollinger + Donchian breakout whose lookback window self-adjusts to the change in
30-bar volatility (clamped to ``[floor_amt, ceiling_amt]``). Long-only: a bar
closing over the (previous) upper Bollinger band with a break of the (previous)
Donchian upper buys; the position sells on a lower breakout or a cross back below
the adaptive mid-line.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DynamicBreakoutLongConfig:
    """User-facing parameters for the Dynamic Breakout II long strategy."""

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
