"""Superman System short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``SupermanSystem_S`` system — a channel
breakout filtered by a market-strength index and a momentum turn: short a
downside channel breakout when the strength index is strongly bearish and
momentum has flipped from up to down, protected by a channel stop, a profit
target a multiple of the stop distance away, and a reverse-signal exit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupermanShortConfig:
    """User-facing parameters for the Superman System short strategy."""

    length: int = 5             # Length: strength-index & entry-channel period
    stop_len: int = 5           # Stop_Len: stop-channel (Highest high) period
    profit_factor: float = 3.0  # ProfitFactor: profit target as a multiple of the stop distance
    entry_strength: float = 95.0  # EntryStrength: |strength index| threshold for entry / reverse
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("length", "stop_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.profit_factor < 0:
            raise ValueError("profit_factor must be >= 0.")
        if self.entry_strength < 0:
            raise ValueError("entry_strength must be >= 0.")
