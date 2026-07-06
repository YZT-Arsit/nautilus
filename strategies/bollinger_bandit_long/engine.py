"""Bollinger Bandit long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``BollingerBandit_L`` system (long mirror of
``BollingerBandit_S``):

* ``MidLine = Average(Close, bollingerLengths)``, ``Band = StandardDev(Close,
  bollingerLengths)``, ``upBand = MidLine + Offset*Band``;
* momentum filter ``rocCalc = Close - Close[rocCalcLength - 1]``;
* entry (long): flat, ``rocCalc[1] > 0`` (momentum up) and ``High >= upBand[1]``
  -> long at ``Max(Open, upBand[1])``;
* an **adaptive** exit MA: ``liqDays`` resets to ``liqLength`` while flat and
  decrements by one each in-trade bar (floored at ``liq_floor``), so ``liqPoint =
  Average(Close, liqDays)`` tightens the longer the long is held;
* exit (sell), once ``BarsSinceEntry >= 1``: ``liqPoint[1] < upBand[1]`` (the exit
  MA sits below the upper band) and ``Low <= liqPoint[1]`` -> sell at ``Min(Open,
  liqPoint[1])``.

Faithful TradeBlazer semantics preserved: the entry reads ``rocCalc[1]`` /
``upBand[1]`` and the exit reads ``liqPoint[1]`` / ``upBand[1]`` (all previous-bar
values snapshotted before the roll), while triggers use the current ``High`` /
``Low`` / ``Open``; ``liqDays`` uses the bar-start ``MarketPosition`` (so it resets
on the entry bar and only starts shrinking the next bar); the exit is gated by
``BarsSinceEntry >= 1`` so entry and sell never fire on one bar. There is **no**
``Vol > 0`` gate (matches the source). ``StandardDev`` uses the sample divisor
(``ddof=1``, TB DataType 2); ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import rolling_std

from strategies.bollinger_bandit_long.config import BollingerBanditLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class BollingerBanditLongEngine:
    """Pure, position-aware Bollinger Bandit long engine."""

    def __init__(self, config: BollingerBanditLongConfig) -> None:
        self.cfg = config
        maxlen = max(config.bollinger_lengths, config.roc_calc_length, config.liq_length)
        self._closes: deque[float] = deque(maxlen=maxlen)

        self.liq_days = config.liq_length

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_upband: float | None = None
        self._prev_roc: float | None = None
        self._prev_liqpoint: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sum(list(self._closes)[-period:]) / period

    def _sma_avail(self, period: int) -> float | None:
        """SMA over the last ``min(period, len)`` closes (TB Average during warmup)."""
        if not self._closes:
            return None
        n = min(period, len(self._closes))
        return sum(list(self._closes)[-n:]) / n

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._closes.append(close)

        # 1. Bollinger upper band.
        midline = self._sma(cfg.bollinger_lengths)
        band = rolling_std(list(self._closes)[-cfg.bollinger_lengths:], ddof=1) if len(self._closes) >= cfg.bollinger_lengths else None
        upband = midline + cfg.offset * band if midline is not None and band is not None else None

        # 2. Momentum filter rocCalc = Close - Close[rocCalcLength - 1].
        roc = (
            close - list(self._closes)[-cfg.roc_calc_length]
            if len(self._closes) >= cfg.roc_calc_length else None
        )

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open long): momentum-up filter + break of the prior upper band.
        if (
            not acted and mp_start != 1
            and self._prev_roc is not None and self._prev_roc > 0
            and self._prev_upband is not None and high >= self._prev_upband
        ):
            entry_price = max(open_, self._prev_upband)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = BUY, "enter_long", True

        # 4. Adaptive exit-MA period (resets flat, shrinks in-trade; bar-start MP).
        if mp_start == 0:
            self.liq_days = cfg.liq_length
        else:
            self.liq_days = max(self.liq_days - 1, cfg.liq_floor)
        liqpoint = self._sma_avail(self.liq_days)

        # 5. EXIT (sell): exit MA below the upper band, price breaks the exit MA.
        if (
            not acted and mp_start == 1 and self.bars_since_entry >= 1
            and self._prev_liqpoint is not None and self._prev_upband is not None
            and self._prev_liqpoint < self._prev_upband and low <= self._prev_liqpoint
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = SELL, "exit_liq_ma", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_upband = upband
        self._prev_roc = roc
        self._prev_liqpoint = liqpoint
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
