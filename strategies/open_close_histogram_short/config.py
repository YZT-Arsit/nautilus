"""Open/Close Histogram short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Open_Close_Histogram_S`` system — a
histogram of ``EMA(Close) - EMA(Open)`` whose zero-cross defines the trend; on a
downside cross it arms ATR-offset short entry / exit triggers, shorts a break of
the entry trigger, and covers on a reverse cross or the exit trigger.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenCloseHistogramShortConfig:
    """User-facing parameters for the Open/Close Histogram short strategy."""

    open_len: int = 10       # OpenLen: EMA period for the open price
    close_len: int = 10      # CloseLen: EMA period for the close price
    atr_len: int = 10        # ATR period for the trigger offsets (fixed 10 in the source)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("open_len", "close_len", "atr_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
