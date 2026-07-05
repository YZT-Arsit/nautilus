"""Displaced-Bollinger short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``DisplacedBoll_S`` system — a breakout of a
Bollinger channel whose **mid-line is displaced back** ``Disp`` bars while the band
width uses the current standard deviation. Short-only: a break of the (previous)
lower band shorts; the position covers on a break of the (previous) upper band.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DisplacedBollShortConfig:
    """User-facing parameters for the Displaced-Bollinger short strategy."""

    avg_len: int = 3       # AvgLen: mid-line SMA period
    disp: int = 16         # Disp: bars the mid-line is displaced back
    sd_len: int = 12       # SDLen: standard-deviation period
    sdev: float = 2.0      # SDev: band standard-deviation multiple
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("avg_len", "disp", "sd_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.sdev < 0:
            raise ValueError("sdev must be >= 0.")
