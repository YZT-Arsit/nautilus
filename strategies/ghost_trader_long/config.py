"""Ghost Trader long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``GhostTrader_L`` system — the long mirror of
``ghost_trader_short``: a "ghost trader" that runs a **simulated** long (fast EMA
above slow EMA, RSI below the overbought level, a new high) and only sends a
**real** long once the most recent simulated trade closed at a loss. It exits
(both simulated and real) on a break below the 20-bar Donchian lower channel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GhostTraderLongConfig:
    """User-facing parameters for the Ghost Trader long strategy."""

    fast_length: int = 9      # FastLength: fast EMA period
    slow_length: int = 19     # SlowLength: slow EMA period
    rsi_length: int = 9       # Length: RSI period
    over_sold: float = 30     # OverSold: (unused on the long side; kept for parity)
    over_bought: float = 70   # OverBought: RSI ceiling gate for the long setup
    donchian_length: int = 20  # Donchian channel period (Highest(High)/Lowest(Low))
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.fast_length <= 0 or self.slow_length <= 0:
            raise ValueError("fast_length and slow_length must be > 0.")
        if self.fast_length >= self.slow_length:
            raise ValueError("fast_length must be < slow_length.")
        if self.rsi_length <= 0:
            raise ValueError("rsi_length must be > 0.")
        if self.donchian_length <= 0:
            raise ValueError("donchian_length must be > 0.")
