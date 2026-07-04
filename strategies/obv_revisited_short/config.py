"""OBV Revisited short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``OBVRevisited_S`` system — a
volatility-weighted OBV (WOBV) crossed against its own moving average: a
down-cross arms a short trigger at that bar's low, a break of the trigger opens
the short, and an up-cross covers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObvRevisitedShortConfig:
    """User-facing parameters for the OBV Revisited short strategy."""

    avg_length: int = 25     # AvgLength: MA period of the WOBV
    tick: float = 0.01       # MinMove * PriceScale: one price tick (breakout buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.avg_length <= 0:
            raise ValueError("avg_length must be > 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
