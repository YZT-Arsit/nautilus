"""Trend-breakout + ATR strategy configuration (user-facing parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Behaviour and defaults are unchanged from the original single-file
module - this split is purely structural.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendBreakoutAtrConfig:
    """User-facing parameters for the trend-breakout + ATR strategy."""

    trend_len: int = 120        # slow MA for the trend filter (~2h of 1m bars)
    breakout_len: int = 60      # rolling high/low window (~1h)
    atr_len: int = 30           # ATR window
    atr_mult_stop: float = 2.0  # hard stop = entry -/+ atr_mult_stop * ATR
    atr_mult_exit: float = 1.0  # give-back exit from best favourable close
    cooldown_bars: int = 30     # bars to wait flat after any close before re-entry
    min_atr_pct: float = 0.0005  # volatility filter: ATR/close must be >= this
    allow_short: bool = True
    quantity: float = 1.0       # informational; sizing lives in execution config
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None
