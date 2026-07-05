"""Displaced-Bollinger short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``DisplacedBoll_S`` system:

* ``AvgVal = Average(Close, AvgLen)``; ``SDmult = StandardDev(Close, SDLen) *
  SDev``;
* the channel mid-line is **displaced back** ``Disp`` bars while the width is
  current: ``DispTop = AvgVal[Disp] + SDmult``, ``DispBottom = AvgVal[Disp] -
  SDmult``;
* entry (short): flat and ``Low <= DispBottom[1]`` -> short at ``Min(Open,
  DispBottom[1])``;
* exit (cover), once ``BarsSinceEntry > 0``: ``High >= DispTop[1]`` -> cover at
  ``Max(Open, DispTop[1])``.

Faithful TradeBlazer semantics preserved: the entry/exit read the **previous-bar**
band levels ``DispBottom[1]`` / ``DispTop[1]`` (snapshotted before the roll), while
the trigger uses the current bar's ``Low`` / ``High`` / ``Open``; the mid-line uses
the SMA value ``Disp`` bars ago combined with the **current** standard deviation;
``MarketPosition`` uses the bar-start position and the exit is gated by
``BarsSinceEntry > 0`` so entry and cover never fire on one bar. There is **no**
``Vol > 0`` gate (matches the source). ``StandardDev`` uses the sample divisor
(``ddof=1``, TB DataType 2); ``Average`` is a simple mean.
"""
from __future__ import annotations

import math
from collections import deque

from strategies.displaced_boll_short.config import DisplacedBollShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


def _std(vals: list[float], ddof: int) -> float:
    n = len(vals)
    if n - ddof <= 0:
        return 0.0
    mean = sum(vals) / n
    var = sum((x - mean) ** 2 for x in vals) / (n - ddof)
    return math.sqrt(var)


class DisplacedBollShortEngine:
    """Pure, position-aware Displaced-Bollinger short engine."""

    def __init__(self, config: DisplacedBollShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=max(config.avg_len, config.sd_len))
        self._ma_hist: deque[float | None] = deque(maxlen=config.disp + 1)

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar band snapshots (the ``[1]`` values the decisions read)
        self._prev_top: float | None = None
        self._prev_bottom: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sum(list(self._closes)[-period:]) / period

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._closes.append(close)

        # 1. Mid-line SMA + its displaced value, and the current-width bands.
        ma = self._sma(cfg.avg_len)
        self._ma_hist.append(ma)
        avg_disp = (
            self._ma_hist[0]
            if len(self._ma_hist) == cfg.disp + 1 and self._ma_hist[0] is not None
            else None
        )
        sd = _std(list(self._closes)[-cfg.sd_len:], ddof=1) if len(self._closes) >= cfg.sd_len else None
        sdmult = sd * cfg.sdev if sd is not None else None
        if avg_disp is not None and sdmult is not None:
            disptop = avg_disp + sdmult
            dispbottom = avg_disp - sdmult
        else:
            disptop = dispbottom = None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): break of the previous lower band.
        if (
            not acted and mp_start == 0
            and self._prev_bottom is not None and low <= self._prev_bottom
        ):
            entry_price = min(open_, self._prev_bottom)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): break of the previous upper band.
        if (
            not acted and mp_start == -1 and self.bars_since_entry > 0
            and self._prev_top is not None and high >= self._prev_top
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = BUY, "exit_channel", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_top = disptop
        self._prev_bottom = dispbottom
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
