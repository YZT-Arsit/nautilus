"""Trading Range Breakout short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Trading_Range_Breakout_S`` system — detect
a quiet ``range_len``-bar consolidation (the summed "gaps" between each bar's
extreme and the range extreme are large relative to the range height), then short
a downside breakout bar, protected by an initial stop at the range high, an ATR
trailing stop off the profit-low, and a bullish-reversal exit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingRangeBreakoutShortConfig:
    """User-facing parameters for the Trading Range Breakout short strategy."""

    range_len: int = 7        # RangeLen: high/low & ATR-MA look-back window
    rng_pcnt: float = 200.0   # RngPcnt: gap-sum threshold as % of range height (200 == 2x)
    atr_s: float = 8.0        # ATRs: trailing-stop ATR multiple off the profit low
    atr_len: int = 2          # ATRLen: trailing-stop ATR period
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("range_len", "atr_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.rng_pcnt < 0:
            raise ValueError("rng_pcnt must be >= 0.")
        if self.atr_s < 0:
            raise ValueError("atr_s must be >= 0.")
