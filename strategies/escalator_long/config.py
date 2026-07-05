"""Escalator long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Escalator_L`` system — a moving-average +
candlestick-pattern high/low breakout: a bullish MA regime (price above both MAs)
plus a two-bar close-position pattern (close-near-low then close-near-high) buys a
break of the recent high channel, exiting on an ATR-free stop (recent low) or a
risk-multiple profit target.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EscalatorLongConfig:
    """User-facing parameters for the Escalator long strategy."""

    fast_length: int = 8       # FastLength: fast SMA period
    slow_length: int = 40      # SlowLength: slow SMA period
    risk_length: int = 2       # RiskLength: low-channel period for the stop
    profit_factor: float = 2.0  # ProfitFactor: profit target as a multiple of risk
    tick: float = 0.01         # MinMove * PriceScale: one price tick (breakout buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("fast_length", "slow_length", "risk_length"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.profit_factor < 0:
            raise ValueError("profit_factor must be >= 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
