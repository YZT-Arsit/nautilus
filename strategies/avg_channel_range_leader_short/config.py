"""Average-Channel Range-Leader short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``AverageChannelRangeLeader_S`` system — a
displaced high/low moving-average channel combined with a "range leader" bar
(median below the prior low with an expanding range). Short-only: a prior
range-leader bar closing below the displaced low-MA shorts; the stop switches from
the mid channel to the outer (low) channel after ``ExitBar`` bars.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvgChannelRangeLeaderShortConfig:
    """User-facing parameters for the Average-Channel Range-Leader short strategy."""

    avg_len: int = 20      # AvgLen: high/low MA period
    abs_disp: int = 5      # AbsDisp: bars the high/low MAs are displaced back
    exit_bar: int = 5      # ExitBar: mid-channel stop up to here, outer-channel stop after
    tick: float = 0.01     # MinMove * PriceScale: one price tick
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("avg_len", "abs_disp", "exit_bar"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
