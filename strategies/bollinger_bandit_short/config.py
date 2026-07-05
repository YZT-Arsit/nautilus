"""Bollinger Bandit short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``BollingerBandit_S`` system — a Bollinger
lower-band breakout gated by a rate-of-change filter, exited by an adaptive-length
moving average whose period shrinks the longer the position is held. Short-only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BollingerBanditShortConfig:
    """User-facing parameters for the Bollinger Bandit short strategy."""

    bollinger_lengths: int = 50   # bollingerLengths: mid-line SMA / std period
    offset: float = 1.25          # Offset: lower-band standard-deviation multiple
    roc_calc_length: int = 30     # rocCalcLength: momentum filter lookback
    liq_length: int = 50          # liqLength: initial (max) exit-MA period
    liq_floor: int = 10           # the exit-MA period floor as it shrinks in-trade
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("bollinger_lengths", "roc_calc_length", "liq_length", "liq_floor"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.liq_floor > self.liq_length:
            raise ValueError("liq_floor must be <= liq_length.")
        if self.offset < 0:
            raise ValueError("offset must be >= 0.")
