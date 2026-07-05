"""Average-Channel Range-Leader short — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``AverageChannelRangeLeader_S`` system:

* displaced high/low/median MA channel: ``LowerAvg = Average(Low[AbsDisp],
  AvgLen)`` and ``ExitAvg = Average(MedianPrice[AbsDisp], AvgLen)`` — i.e. the
  ``AvgLen``-bar SMA of Low / (H+L)/2 displaced back ``AbsDisp`` bars (the upper MA
  is unused on the short side);
* a "range leader" bar ``RangeLeadS = MedianPrice < Low[1] And MyRange >
  MyRange[1]`` (median under the prior low with an expanding range);
* entry (short): flat, ``RangeLeadS[1]`` and ``Close[1] < LowerAvg[1]`` -> short at
  ``Open``;
* exit (cover), once ``BarsSinceEntry > 0``: for the first ``ExitBar`` bars the
  mid-channel stop ``High >= ExitAvg`` -> cover at ``Max(Open, ExitAvg)``; after
  ``ExitBar`` bars the outer stop ``High >= LowerAvg + tick`` -> cover at
  ``Max(Open, LowerAvg + tick)``.

Faithful TradeBlazer semantics preserved: the **entry** reads the previous-bar
``RangeLeadS[1]`` / ``Close[1]`` / ``LowerAvg[1]`` (snapshotted before the roll)
while the **exit** reads the current-bar displaced ``ExitAvg`` / ``LowerAvg`` (both
already lagged ``AbsDisp`` bars); ``RangeLeadS`` compares the current median to the
prior low and the current range to the prior range; ``MarketPosition`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0`` so entry and
cover never fire on one bar. ``BarsSinceEntry <= ExitBar`` selects the mid stop,
``> ExitBar`` the outer stop. There is **no** ``Vol > 0`` gate (matches the source).
``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from strategies.avg_channel_range_leader_short.config import AvgChannelRangeLeaderShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class AvgChannelRangeLeaderShortEngine:
    """Pure, position-aware Average-Channel Range-Leader short engine."""

    def __init__(self, config: AvgChannelRangeLeaderShortConfig) -> None:
        self.cfg = config
        self._lows: deque[float] = deque(maxlen=config.avg_len)
        self._medians: deque[float] = deque(maxlen=config.avg_len)
        self._lowma_hist: deque[float | None] = deque(maxlen=config.abs_disp + 1)
        self._medma_hist: deque[float | None] = deque(maxlen=config.abs_disp + 1)

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_low: float | None = None
        self._prev_range: float | None = None
        self._prev_close: float | None = None
        self._prev_range_lead = False
        self._prev_lower_avg: float | None = None

    @staticmethod
    def _mean(dq: deque[float], period: int) -> float | None:
        if len(dq) < period:
            return None
        return sum(dq) / len(dq)

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        myrange = high - low
        median = (high + low) * 0.5

        # 1. Non-displaced SMAs of Low and MedianPrice, then their displaced values.
        self._lows.append(low)
        self._medians.append(median)
        lowma = self._mean(self._lows, cfg.avg_len)
        medma = self._mean(self._medians, cfg.avg_len)
        self._lowma_hist.append(lowma)
        self._medma_hist.append(medma)
        lower_avg = (
            self._lowma_hist[0]
            if len(self._lowma_hist) == cfg.abs_disp + 1 and self._lowma_hist[0] is not None
            else None
        )
        exit_avg = (
            self._medma_hist[0]
            if len(self._medma_hist) == cfg.abs_disp + 1 and self._medma_hist[0] is not None
            else None
        )

        # 2. Range-leader flag (current bar).
        range_lead = (
            self._prev_low is not None and self._prev_range is not None
            and median < self._prev_low and myrange > self._prev_range
        )

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open short): prior range-leader bar closing below the low MA.
        if (
            not acted and mp_start == 0 and self._prev_range_lead
            and self._prev_close is not None and self._prev_lower_avg is not None
            and self._prev_close < self._prev_lower_avg
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = open_
            signal, reason, acted = SELL, "enter_short", True

        # 4. EXIT (cover): mid-channel stop early, outer (low) channel stop later.
        if not acted and mp_start == -1 and self.bars_since_entry > 0:
            if self.bars_since_entry <= cfg.exit_bar:
                if exit_avg is not None and high >= exit_avg:
                    self._cover()
                    signal, reason, acted = BUY, "exit_mid_stop", True
            else:
                if lower_avg is not None and high >= lower_avg + cfg.tick:
                    self._cover()
                    signal, reason, acted = BUY, "exit_outer_stop", True

        # 5. Roll the prev-bar snapshots, then advance counters.
        self._prev_low = low
        self._prev_range = myrange
        self._prev_close = close
        self._prev_range_lead = range_lead
        self._prev_lower_avg = lower_avg
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
