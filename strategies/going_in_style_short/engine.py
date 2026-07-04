"""Going in Style short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Going_in_Style_S`` system:

* ``Condition2 = Low < Lowest(Low[1], Length)`` — this bar made a new low;
* entry (short): ``Condition2[1]`` (the prior bar made a new low) and ``Low <=
  Close[1] - ATR[1]*Trigger`` and ``Vol > 0`` -> short at ``Min(Open, Close[1] -
  ATR[1]*Trigger)``;
* a parabolic-SAR-style trailing stop: on the entry bar ``StopPrice = High +
  StopATR*FirstBarMultp`` (``StopATR = Average(TrueRange, 3)``), ``AF =
  Acceleration``, ``LowValue = Low``; on later bars ``LowValue`` tracks the lowest
  low, ``AF`` steps up by ``Acceleration`` (capped at 0.2) on each new low, and
  ``StopPrice = StopPrice - AF*(StopPrice - LowValue)``;
* exit (cover): ``BarsSinceEntry > 0``, ``High >= StopPrice[1]`` and ``Vol > 0``
  -> cover at ``Max(Open, StopPrice[1])``.

Faithful TradeBlazer semantics preserved: the entry reads ``Condition2[1]`` /
``Close[1]`` / ``ATR[1]`` and the exit reads ``StopPrice[1]`` (previous-bar values
snapshotted before the roll); ``StopPrice`` / ``LowValue`` / ``AF`` persist as
running state; ``MarketPosition`` uses the bar-start position and the exit is
gated by ``BarsSinceEntry > 0``, so entry and cover never fire on one bar. There
**is** a ``Vol > 0`` gate. ``ATR`` / ``StopATR`` are simple means of true range
(the TradeBlazer builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from strategies.going_in_style_short.config import GoingInStyleShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_AF_CAP = 0.2


class GoingInStyleShortEngine:
    """Pure, position-aware Going in Style short engine."""

    def __init__(self, config: GoingInStyleShortConfig) -> None:
        self.cfg = config
        self._lows: deque[float] = deque(maxlen=config.length)   # Lowest(Low[1], Length)
        self._trs_len: deque[float] = deque(maxlen=config.length)  # ATR(Length)
        self._trs_3: deque[float] = deque(maxlen=3)               # StopATR = Average(TrueRange, 3)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent trailing-stop state
        self.stop_price: float | None = None
        self.low_value: float | None = None
        self.af = config.acceleration

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_condition2 = False
        self._prev_close: float | None = None
        self._prev_atr: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. True range, ATR(Length) and StopATR = Average(TrueRange, 3).
        tr = high - low if self._tr_prev_close is None else max(
            high - low, abs(high - self._tr_prev_close), abs(low - self._tr_prev_close)
        )
        # Lowest(Low[1], Length) — prior lows before this bar.
        lowest_prior = min(self._lows) if self._lows else None
        condition2 = lowest_prior is not None and low < lowest_prior

        self._trs_len.append(tr)
        self._trs_3.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs_len) / len(self._trs_len) if len(self._trs_len) == cfg.length else None
        stopatr = sum(self._trs_3) / len(self._trs_3) if len(self._trs_3) == 3 else None

        # Snapshots read by the exit / trailing update.
        prev_stop_price = self.stop_price
        prev_low_value = self.low_value

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False
        just_entered = False

        # 2. ENTRY (open short): prior bar made a new low + break of the trigger.
        if (
            not acted and mp_start == 0 and self._prev_condition2
            and self._prev_close is not None and self._prev_atr is not None
        ):
            trigger_price = self._prev_close - self._prev_atr * cfg.trigger
            if low <= trigger_price and volume > 0:
                entry_price = min(open_, trigger_price)
                self.position = -1
                self.bars_since_entry = 0
                self.entry_price = entry_price
                signal, reason, acted = SELL, "enter_short", True
                just_entered = True

        # 3. Parabolic-SAR trailing stop.
        if just_entered:
            self.stop_price = high + (stopatr if stopatr is not None else 0.0) * cfg.first_bar_multp
            self.af = cfg.acceleration
            self.low_value = low
        elif mp_start == -1:
            if self.low_value is None or low < self.low_value:
                self.low_value = low
            if prev_low_value is not None and self.low_value < prev_low_value and self.af < _AF_CAP:
                self.af = self.af + min(cfg.acceleration, _AF_CAP - self.af)
            if self.stop_price is not None and self.low_value is not None:
                self.stop_price = self.stop_price - self.af * (self.stop_price - self.low_value)

        # 4. EXIT (cover): price rallies up through the prior trailing stop.
        if (
            not acted and mp_start == -1 and self.bars_since_entry > 0
            and prev_stop_price is not None and high >= prev_stop_price and volume > 0
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = BUY, "exit_stop", True

        # 5. Roll snapshots / history, then advance counters.
        self._prev_condition2 = condition2
        self._prev_close = close
        self._prev_atr = atr
        self._lows.append(low)
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
