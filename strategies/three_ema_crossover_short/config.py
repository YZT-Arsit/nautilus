"""Three EMA Crossover short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Three_EMA_Crossover_System_S`` system —
three exponential moving averages (``Avg1`` fast, ``Avg2`` mid, ``Avg3`` slow);
short when the fast EMA crosses **under** the mid EMA while the mid EMA is below
the slow EMA, exit on the fast EMA crossing back **over** the mid EMA or on a
ratcheting trailing stop derived from the recent bar range.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThreeEmaCrossoverShortConfig:
    """User-facing parameters for the Three EMA Crossover short strategy."""

    avg_len1: int = 6      # AvgLen1: fast EMA period
    avg_len2: int = 12     # AvgLen2: mid EMA period
    avg_len3: int = 28     # AvgLen3: slow EMA period
    r_length: int = 4      # RLength: trailing-stop range look-back (Average(High-Low))
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("avg_len1", "avg_len2", "avg_len3", "r_length"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
