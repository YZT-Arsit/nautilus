"""Bollinger Bandit short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``BollingerBandit_S`` system:

* ``MidLine = Average(Close, bollingerLengths)``, ``Band = StandardDev(Close,
  bollingerLengths)``, ``dnBand = MidLine - Offset*Band``;
* momentum filter ``rocCalc = Close - Close[rocCalcLength - 1]``;
* entry (short): flat, ``rocCalc[1] < 0`` (momentum down) and ``Low <= dnBand[1]``
  -> short at ``Min(Open, dnBand[1])``;
* an **adaptive** exit MA: ``liqDays`` resets to ``liqLength`` while flat and
  decrements by one each in-trade bar (floored at ``liq_floor``), so ``liqPoint =
  Average(Close, liqDays)`` tightens the longer the short is held;
* exit (cover), once ``BarsSinceEntry >= 1``: ``liqPoint[1] > dnBand[1]`` (the exit
  MA sits above the lower band) and ``High >= liqPoint[1]`` -> cover at ``Max(Open,
  liqPoint[1])``.

Faithful TradeBlazer semantics preserved: the entry reads ``rocCalc[1]`` /
``dnBand[1]`` and the exit reads ``liqPoint[1]`` / ``dnBand[1]`` (all previous-bar
values snapshotted before the roll), while triggers use the current ``Low`` /
``High`` / ``Open``; ``liqDays`` uses the bar-start ``MarketPosition`` (so it resets
on the entry bar and only starts shrinking the next bar); the exit is gated by
``BarsSinceEntry >= 1`` so entry and cover never fire on one bar. There is **no**
``Vol > 0`` gate (matches the source). ``StandardDev`` uses the sample divisor
(``ddof=1``, TB DataType 2); ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import rolling_std

from strategies.bollinger_bandit_short.config import BollingerBanditShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class BollingerBanditShortEngine:
    """Pure, position-aware Bollinger Bandit short engine."""

    def __init__(self, config: BollingerBanditShortConfig) -> None:
        self.cfg = config
        maxlen = max(config.bollinger_lengths, config.roc_calc_length, config.liq_length)
        self._closes: deque[float] = deque(maxlen=maxlen)

        self.liq_days = config.liq_length

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_dnband: float | None = None
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

        # 1. Bollinger lower band.
        midline = self._sma(cfg.bollinger_lengths)
        band = rolling_std(list(self._closes)[-cfg.bollinger_lengths:], ddof=1) if len(self._closes) >= cfg.bollinger_lengths else None
        dnband = midline - cfg.offset * band if midline is not None and band is not None else None

        # 2. Momentum filter rocCalc = Close - Close[rocCalcLength - 1].
        roc = (
            close - list(self._closes)[-cfg.roc_calc_length]
            if len(self._closes) >= cfg.roc_calc_length else None
        )

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open short): momentum-down filter + break of the prior lower band.
        if (
            not acted and mp_start != -1
            and self._prev_roc is not None and self._prev_roc < 0
            and self._prev_dnband is not None and low <= self._prev_dnband
        ):
            entry_price = min(open_, self._prev_dnband)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 4. Adaptive exit-MA period (resets flat, shrinks in-trade; bar-start MP).
        if mp_start == 0:
            self.liq_days = cfg.liq_length
        else:
            self.liq_days = max(self.liq_days - 1, cfg.liq_floor)
        liqpoint = self._sma_avail(self.liq_days)

        # 5. EXIT (cover): exit MA above the lower band, price breaks the exit MA.
        if (
            not acted and mp_start == -1 and self.bars_since_entry >= 1
            and self._prev_liqpoint is not None and self._prev_dnband is not None
            and self._prev_liqpoint > self._prev_dnband and high >= self._prev_liqpoint
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = BUY, "exit_liq_ma", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_dnband = dnband
        self._prev_roc = roc
        self._prev_liqpoint = liqpoint
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
