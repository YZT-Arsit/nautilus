"""Average-Channel Range-Leader long — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``AverageChannelRangeLeader_L`` system (long mirror of
``AverageChannelRangeLeader_S``):

* displaced high/median MA channel: ``UpperAvg = Average(High[AbsDisp], AvgLen)``
  and ``ExitAvg = Average(MedianPrice[AbsDisp], AvgLen)`` — i.e. the ``AvgLen``-bar
  SMA of High / (H+L)/2 displaced back ``AbsDisp`` bars (the lower MA is unused on
  the long side);
* a "range leader" bar ``RangeLeadB = MedianPrice > High[1] And MyRange >
  MyRange[1]`` (median over the prior high with an expanding range);
* entry (long): flat, ``RangeLeadB[1]`` and ``Close[1] > UpperAvg[1]`` -> long at
  ``Open``;
* exit (sell), once ``BarsSinceEntry > 0``: for the first ``ExitBar`` bars the
  mid-channel stop ``Low <= ExitAvg`` -> sell at ``Min(Open, ExitAvg)``; after
  ``ExitBar`` bars the outer stop ``Low <= UpperAvg - tick`` -> sell at ``Min(Open,
  UpperAvg - tick)``.

Faithful TradeBlazer semantics preserved: the **entry** reads the previous-bar
``RangeLeadB[1]`` / ``Close[1]`` / ``UpperAvg[1]`` (snapshotted before the roll)
while the **exit** reads the current-bar displaced ``ExitAvg`` / ``UpperAvg`` (both
already lagged ``AbsDisp`` bars); ``RangeLeadB`` compares the current median to the
prior high and the current range to the prior range; ``MarketPosition`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0`` so entry and
sell never fire on one bar. ``BarsSinceEntry <= ExitBar`` selects the mid stop,
``> ExitBar`` the outer stop. There is **no** ``Vol > 0`` gate (matches the source).
``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from strategies.avg_channel_range_leader_long.config import AvgChannelRangeLeaderLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class AvgChannelRangeLeaderLongEngine:
    """Pure, position-aware Average-Channel Range-Leader long engine."""

    def __init__(self, config: AvgChannelRangeLeaderLongConfig) -> None:
        self.cfg = config
        self._highs: deque[float] = deque(maxlen=config.avg_len)
        self._medians: deque[float] = deque(maxlen=config.avg_len)
        self._highma_hist: deque[float | None] = deque(maxlen=config.abs_disp + 1)
        self._medma_hist: deque[float | None] = deque(maxlen=config.abs_disp + 1)

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_high: float | None = None
        self._prev_range: float | None = None
        self._prev_close: float | None = None
        self._prev_range_lead = False
        self._prev_upper_avg: float | None = None

    @staticmethod
    def _mean(dq: deque[float], period: int) -> float | None:
        if len(dq) < period:
            return None
        return sum(dq) / len(dq)

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        myrange = high - low
        median = (high + low) * 0.5

        # 1. Non-displaced SMAs of High and MedianPrice, then their displaced values.
        self._highs.append(high)
        self._medians.append(median)
        highma = self._mean(self._highs, cfg.avg_len)
        medma = self._mean(self._medians, cfg.avg_len)
        self._highma_hist.append(highma)
        self._medma_hist.append(medma)
        upper_avg = (
            self._highma_hist[0]
            if len(self._highma_hist) == cfg.abs_disp + 1 and self._highma_hist[0] is not None
            else None
        )
        exit_avg = (
            self._medma_hist[0]
            if len(self._medma_hist) == cfg.abs_disp + 1 and self._medma_hist[0] is not None
            else None
        )

        # 2. Range-leader flag (current bar).
        range_lead = (
            self._prev_high is not None and self._prev_range is not None
            and median > self._prev_high and myrange > self._prev_range
        )

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open long): prior range-leader bar closing above the high MA.
        if (
            not acted and mp_start == 0 and self._prev_range_lead
            and self._prev_close is not None and self._prev_upper_avg is not None
            and self._prev_close > self._prev_upper_avg
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = open_
            signal, reason, acted = BUY, "enter_long", True

        # 4. EXIT (sell): mid-channel stop early, outer (high) channel stop later.
        if not acted and mp_start == 1 and self.bars_since_entry > 0:
            if self.bars_since_entry <= cfg.exit_bar:
                if exit_avg is not None and low <= exit_avg:
                    self._sell()
                    signal, reason, acted = SELL, "exit_mid_stop", True
            else:
                if upper_avg is not None and low <= upper_avg - cfg.tick:
                    self._sell()
                    signal, reason, acted = SELL, "exit_outer_stop", True

        # 5. Roll the prev-bar snapshots, then advance counters.
        self._prev_high = high
        self._prev_range = myrange
        self._prev_close = close
        self._prev_range_lead = range_lead
        self._prev_upper_avg = upper_avg
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _sell(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
