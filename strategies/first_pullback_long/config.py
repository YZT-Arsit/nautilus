"""First-PullBack long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``FirstPullBackSys_L`` system — a MACD-based
pullback long: the MACD signal line crossing above zero flags an uptrend; while in
that uptrend a Close/ATR channel is armed and price breaking the upper band opens
a long, sold when the trend ends or price drops through the lower / trend-low
bands.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FirstPullbackLongConfig:
    """User-facing parameters for the First-PullBack long strategy."""

    fast_ma: int = 4          # FastMA: MACD fast EMA period
    slow_ma: int = 10         # SlowMA: MACD slow EMA period
    avg_ma: int = 16          # AvgMA: MACD signal-line EMA period
    atr_len: int = 10         # ATRLen: ATR period
    entry_atr_pcnt: float = 1.0   # EATRPcnt: entry-channel ATR multiple
    exit_atr_pcnt: float = 1.0    # XATRPcnt: exit-channel ATR multiple
    tick: float = 0.01        # MinMove * PriceScale: one price tick (exit buffer)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("fast_ma", "slow_ma", "avg_ma", "atr_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        for name in ("entry_atr_pcnt", "exit_atr_pcnt", "tick"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0.")
