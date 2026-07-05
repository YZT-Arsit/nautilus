"""Four-MA Crossover short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``FourSetofMACrossoverSys_S`` system — a
four-moving-average system: two SMA pairs (a 5/20 "entry" group and a 3/10 "exit"
group). A short opens when both pairs are bearishly arranged and price makes a
lower low; it covers when the short exit group flips bullish.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FourMaCrossoverShortConfig:
    """User-facing parameters for the Four-MA Crossover short strategy."""

    se_fast: int = 5    # SEFast: short-entry fast SMA
    se_slow: int = 20   # SESlow: short-entry slow SMA
    sx_fast: int = 3    # SXFast: short-exit fast SMA
    sx_slow: int = 10   # SXSlow: short-exit slow SMA
    le_fast: int = 5    # LEFast: long-entry fast SMA (used by the vestigial exit-bull branch)
    le_slow: int = 20   # LESlow: long-entry slow SMA
    lx_fast: int = 3    # LXFast: long-exit fast SMA
    lx_slow: int = 10   # LXSlow: long-exit slow SMA
    min_bars: int = 100  # CurrentBar >= min_bars gate before the first entry
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("se_fast", "se_slow", "sx_fast", "sx_slow", "le_fast", "le_slow", "lx_fast", "lx_slow"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.min_bars < 0:
            raise ValueError("min_bars must be >= 0.")
