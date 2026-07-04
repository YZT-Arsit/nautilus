"""King Keltner short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``KingKeltner_S`` system — a Keltner-channel
breakout: a typical-price moving average (``(H+L+C)/3``) with a lower band at
``MA - ATR``. A downward-turning MA plus a break below the prior lower band opens
a short; a break back above the MA covers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KingKeltnerShortConfig:
    """User-facing parameters for the King Keltner short strategy."""

    avg_length: int = 40     # avgLength: typical-price moving-average period
    atr_length: int = 40     # atrLength: AvgTrueRange period for the band width
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.avg_length <= 0:
            raise ValueError("avg_length must be > 0.")
        if self.atr_length <= 0:
            raise ValueError("atr_length must be > 0.")
