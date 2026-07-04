"""MA-Crossover Channel-Breakout short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``MovingAverageCrossOver_S`` system — the
short mirror of ``ma_crossover_channel_long``: a fast/slow MA crossover that arms
a channel-breakout short entry, with a trend-reversal exit, a periodic-high
trailing stop, and a post-trailing-stop re-entry. Registered as
``ma_crossover_channel_short``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaCrossoverChannelShortConfig:
    """User-facing parameters for the MA-Crossover Channel-Breakout short strategy."""

    fast_len: int = 9              # FastLen: fast MA period
    slow_len: int = 18             # SlowLen: slow MA period
    ch_len: int = 12               # ChLen: breakout-channel lookback / valid-window length
    extra_percentage: float = 300  # ExtraPercentage: breakout buffer in bp (300 = 3%)
    trail_bar: int = 8             # TrailBar: highest-high lookback for the trailing stop
    re_bars: int = 15              # ReBars: re-entry must occur within N bars of the stop
    re_entry_ch_len: int = 10      # ReEntryChLen: re-entry breakout-channel lookback
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.fast_len <= 0 or self.slow_len <= 0:
            raise ValueError("fast_len and slow_len must be > 0.")
        if self.fast_len >= self.slow_len:
            raise ValueError("fast_len must be < slow_len.")
        if self.ch_len <= 0 or self.re_entry_ch_len <= 0:
            raise ValueError("ch_len and re_entry_ch_len must be > 0.")
        if self.trail_bar <= 0:
            raise ValueError("trail_bar must be > 0.")
        if self.re_bars < 0:
            raise ValueError("re_bars must be >= 0.")
