"""Four-MA Crossover long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``FourSetofMACrossoverSys_L`` system — a
four-moving-average system: two SMA pairs (a 5/20 "entry" group and a 3/10 "exit"
group). A long opens when both pairs are bullishly arranged and price makes a
higher high; it sells when the long exit group flips bearish.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FourMaCrossoverLongConfig:
    """User-facing parameters for the Four-MA Crossover long strategy."""

    le_fast: int = 5    # LEFast: long-entry fast SMA
    le_slow: int = 20   # LESlow: long-entry slow SMA
    lx_fast: int = 3    # LXFast: long-exit fast SMA
    lx_slow: int = 10   # LXSlow: long-exit slow SMA
    se_fast: int = 5    # SEFast: short-entry fast SMA (used by the vestigial exit-bear branch)
    se_slow: int = 20   # SESlow: short-entry slow SMA
    sx_fast: int = 3    # SXFast: short-exit fast SMA
    sx_slow: int = 10   # SXSlow: short-exit slow SMA
    min_bars: int = 100  # CurrentBar >= min_bars gate before the first entry
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("le_fast", "le_slow", "lx_fast", "lx_slow", "se_fast", "se_slow", "sx_fast", "sx_slow"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.min_bars < 0:
            raise ValueError("min_bars must be >= 0.")
