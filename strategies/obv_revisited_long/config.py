"""OBV Revisited long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``OBVRevisited_L`` system — the long mirror
of ``OBVRevisited_S``. A volatility-weighted OBV (WOBV) crossed against its own
moving average: an up-cross arms a long trigger at that bar's high, a break of the
trigger opens the long, and a down-cross sells.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObvRevisitedLongConfig:
    """User-facing parameters for the OBV Revisited long strategy."""

    avg_length: int = 25     # AvgLength: MA period of the WOBV
    tick: float = 0.01       # MinMove * PriceScale: one price tick (breakout buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.avg_length <= 0:
            raise ValueError("avg_length must be > 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
