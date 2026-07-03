"""Trading Range Breakout long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Trading_Range_Breakout_L`` system — the
long-side mirror of ``Trading_Range_Breakout_S``. Detect a quiet ``range_len``-bar
consolidation (large summed "gaps"), then go long an upside breakout bar,
protected by an initial stop at the range low, an ATR trailing stop off the
profit-high, and a bearish-reversal exit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingRangeBreakoutLongConfig:
    """User-facing parameters for the Trading Range Breakout long strategy."""

    range_len: int = 7        # RangeLen: high/low & ATR-MA look-back window
    rng_pcnt: float = 200.0   # RngPcnt: gap-sum threshold as % of range height (200 == 2x)
    atr_s: float = 8.0        # ATRs: trailing-stop ATR multiple off the profit high
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
