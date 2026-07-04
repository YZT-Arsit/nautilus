"""King Keltner long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``KingKeltner_L`` system — the long mirror of
``king_keltner_short``: a typical-price moving average (``(H+L+C)/3``) with an
upper band at ``MA + ATR``. An upward-turning MA plus a break above the prior
upper band opens a long; a break back below the MA flattens it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KingKeltnerLongConfig:
    """User-facing parameters for the King Keltner long strategy."""

    avg_length: int = 40     # avgLength: typical-price moving-average period
    atr_length: int = 40     # atrLength: AvgTrueRange period for the band width
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.avg_length <= 0:
            raise ValueError("avg_length must be > 0.")
        if self.atr_length <= 0:
            raise ValueError("atr_length must be > 0.")
