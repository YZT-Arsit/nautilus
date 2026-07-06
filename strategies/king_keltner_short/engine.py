"""King Keltner short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``KingKeltner_S`` system:

* ``movAvgVal = Average((High + Low + Close) / 3, avgLength)`` — a typical-price
  moving average;
* ``dnBand = movAvgVal - AvgTrueRange(atrLength)`` — the lower Keltner band;
* ``liquidPoint = movAvgVal`` — the exit reference;
* entry (short): ``MarketPosition != -1``, ``movAvgVal[1] < movAvgVal[2]`` (the MA
  turned down) and ``Low <= dnBand[1]`` -> short at ``Min(Open, dnBand[1])``;
* exit (cover): ``MarketPosition == -1``, ``BarsSinceEntry >= 1`` and ``High >=
  liquidPoint[1]`` -> cover at ``Max(Open, liquidPoint[1])``.

Faithful TradeBlazer semantics preserved: the entry reads ``movAvgVal[1]`` /
``movAvgVal[2]`` / ``dnBand[1]`` and the exit reads ``liquidPoint[1]`` (==
``movAvgVal[1]``) — all previous-bar values snapshotted before the roll;
``MarketPosition`` uses the bar-start position and the exit is gated by
``BarsSinceEntry >= 1``, so entry and cover never fire on the same bar. There is
**no** ``Vol > 0`` gate in the source. ``AvgTrueRange`` is a simple mean of true
range over ``atr_length`` (matches the other ports; the TradeBlazer builtin uses
Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range
from strategies.king_keltner_short.config import KingKeltnerShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class KingKeltnerShortEngine:
    """Pure, position-aware King Keltner short engine."""

    def __init__(self, config: KingKeltnerShortConfig) -> None:
        self.cfg = config
        self._typicals: deque[float] = deque(maxlen=config.avg_length)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_mav: float | None = None    # movAvgVal[1] (== liquidPoint[1])
        self._prev2_mav: float | None = None   # movAvgVal[2]
        self._prev_dn: float | None = None      # dnBand[1]

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Typical-price MA and the lower Keltner band.
        typical = (high + low + close) / 3
        self._typicals.append(typical)
        mav = sum(self._typicals) / len(self._typicals) if len(self._typicals) == cfg.avg_length else None

        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None
        dn = mav - atr if (mav is not None and atr is not None) else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): MA turned down + break below the prior lower band.
        if (
            not acted and mp_start != -1
            and self._prev_mav is not None and self._prev2_mav is not None
            and self._prev_mav < self._prev2_mav
            and self._prev_dn is not None and low <= self._prev_dn
        ):
            entry_price = min(open_, self._prev_dn)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): break back above the prior MA.
        if (
            not acted and mp_start == -1 and self.bars_since_entry >= 1
            and self._prev_mav is not None and high >= self._prev_mav
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = BUY, "exit_cover", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev2_mav = self._prev_mav
        self._prev_mav = mav
        self._prev_dn = dn
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
