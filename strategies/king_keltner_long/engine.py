"""King Keltner long — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``KingKeltner_L`` system — the long mirror of
``king_keltner_short``:

* ``movAvgVal = Average((High + Low + Close) / 3, avgLength)`` — a typical-price
  moving average;
* ``upBand = movAvgVal + AvgTrueRange(atrLength)`` — the upper Keltner band;
* ``liquidPoint = movAvgVal`` — the exit reference;
* entry (long): ``MarketPosition != 1``, ``movAvgVal[1] > movAvgVal[2]`` (the MA
  turned up) and ``High >= upBand[1]`` -> long at ``Max(Open, upBand[1])``;
* exit (flatten): ``MarketPosition == 1``, ``BarsSinceEntry >= 1`` and ``Low <=
  liquidPoint[1]`` -> sell at ``Min(Open, liquidPoint[1])``.

Faithful TradeBlazer semantics preserved: the entry reads ``movAvgVal[1]`` /
``movAvgVal[2]`` / ``upBand[1]`` and the exit reads ``liquidPoint[1]`` (==
``movAvgVal[1]``) — all previous-bar values snapshotted before the roll;
``MarketPosition`` uses the bar-start position and the exit is gated by
``BarsSinceEntry >= 1``, so entry and sell never fire on the same bar. There is
**no** ``Vol > 0`` gate in the source. ``AvgTrueRange`` is a simple mean of true
range over ``atr_length`` (matches the other ports; the TradeBlazer builtin uses
Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, sma, true_range
from strategies.king_keltner_long.config import KingKeltnerLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class KingKeltnerLongEngine:
    """Pure, position-aware King Keltner long engine."""

    def __init__(self, config: KingKeltnerLongConfig) -> None:
        self.cfg = config
        self._typicals: deque[float] = deque(maxlen=config.avg_length)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_mav: float | None = None    # movAvgVal[1] (== liquidPoint[1])
        self._prev2_mav: float | None = None   # movAvgVal[2]
        self._prev_up: float | None = None      # upBand[1]

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Typical-price MA and the upper Keltner band.
        typical = (high + low + close) / 3
        self._typicals.append(typical)
        mav = sma(self._typicals) if len(self._typicals) == cfg.avg_length else None

        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_length)
        up = mav + atr if (mav is not None and atr is not None) else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open long): MA turned up + break above the prior upper band.
        if (
            not acted and mp_start != 1
            and self._prev_mav is not None and self._prev2_mav is not None
            and self._prev_mav > self._prev2_mav
            and self._prev_up is not None and high >= self._prev_up
        ):
            entry_price = max(open_, self._prev_up)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = BUY, "enter_long", True

        # 3. EXIT (flatten): break back below the prior MA.
        if (
            not acted and mp_start == 1 and self.bars_since_entry >= 1
            and self._prev_mav is not None and low <= self._prev_mav
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = SELL, "exit_sell", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev2_mav = self._prev_mav
        self._prev_mav = mav
        self._prev_up = up
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
