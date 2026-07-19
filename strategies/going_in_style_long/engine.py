"""Going in Style long — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Going_in_Style_L`` system — the long mirror of
``going_in_style_short``:

* ``Condition1 = High > Highest(High[1], Length)`` — this bar made a new high;
* entry (long): ``Condition1[1]`` (the prior bar made a new high) and ``High >=
  Close[1] + ATR[1]*Trigger`` and ``Vol > 0`` -> long at ``Max(Open, Close[1] +
  ATR[1]*Trigger)``;
* a parabolic-SAR-style trailing stop: on the entry bar ``StopPrice = Low -
  StopATR*FirstBarMultp`` (``StopATR = Average(TrueRange, 3)``), ``AF =
  Acceleration``, ``HighValue = High``; on later bars ``HighValue`` tracks the
  highest high, ``AF`` steps up by ``Acceleration`` (capped at 0.2) on each new
  high, and ``StopPrice = StopPrice + AF*(HighValue - StopPrice)``;
* exit (flatten): ``BarsSinceEntry > 0``, ``Low <= StopPrice[1]`` and ``Vol > 0``
  -> sell at ``Min(Open, StopPrice[1])``.

Faithful TradeBlazer semantics preserved: the entry reads ``Condition1[1]`` /
``Close[1]`` / ``ATR[1]`` and the exit reads ``StopPrice[1]`` (previous-bar values
snapshotted before the roll); ``StopPrice`` / ``HighValue`` / ``AF`` persist as
running state; ``MarketPosition`` uses the bar-start position and the exit is
gated by ``BarsSinceEntry > 0``, so entry and sell never fire on one bar. There
**is** a ``Vol > 0`` gate. ``ATR`` / ``StopATR`` are simple means of true range
(the TradeBlazer builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, true_range

from strategies.going_in_style_long.config import GoingInStyleLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_AF_CAP = 0.2


class GoingInStyleLongEngine:
    """Pure, position-aware Going in Style long engine."""

    def __init__(self, config: GoingInStyleLongConfig) -> None:
        self.cfg = config
        self._highs: deque[float] = deque(maxlen=config.length)   # Highest(High[1], Length)
        self._trs_len: deque[float] = deque(maxlen=config.length)  # ATR(Length)
        self._trs_3: deque[float] = deque(maxlen=3)               # StopATR = Average(TrueRange, 3)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent trailing-stop state
        self.stop_price: float | None = None
        self.high_value: float | None = None
        self.af = config.acceleration

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_condition1 = False
        self._prev_close: float | None = None
        self._prev_atr: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. True range, ATR(Length) and StopATR = Average(TrueRange, 3).
        tr = true_range(high, low, self._tr_prev_close)
        # Highest(High[1], Length) — prior highs before this bar.
        highest_prior = max(self._highs) if self._highs else None
        condition1 = highest_prior is not None and high > highest_prior

        self._trs_len.append(tr)
        self._trs_3.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs_len, cfg.length)
        stopatr = simple_atr(self._trs_3, 3)

        # Snapshots read by the exit / trailing update.
        prev_stop_price = self.stop_price
        prev_high_value = self.high_value

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False
        just_entered = False

        # 2. ENTRY (open long): prior bar made a new high + break of the trigger.
        if (
            not acted and mp_start == 0 and self._prev_condition1
            and self._prev_close is not None and self._prev_atr is not None
        ):
            trigger_price = self._prev_close + self._prev_atr * cfg.trigger
            if high >= trigger_price and volume > 0:
                entry_price = max(open_, trigger_price)
                self.position = 1
                self.bars_since_entry = 0
                self.entry_price = entry_price
                signal, reason, acted = BUY, "enter_long", True
                just_entered = True

        # 3. Parabolic-SAR trailing stop.
        if just_entered:
            self.stop_price = low - (stopatr if stopatr is not None else 0.0) * cfg.first_bar_multp
            self.af = cfg.acceleration
            self.high_value = high
        elif mp_start == 1:
            if self.high_value is None or high > self.high_value:
                self.high_value = high
            if prev_high_value is not None and self.high_value > prev_high_value and self.af < _AF_CAP:
                self.af = self.af + min(cfg.acceleration, _AF_CAP - self.af)
            if self.stop_price is not None and self.high_value is not None:
                self.stop_price = self.stop_price + self.af * (self.high_value - self.stop_price)

        # 4. EXIT (flatten): price drops down through the prior trailing stop.
        if (
            not acted and mp_start == 1 and self.bars_since_entry > 0
            and prev_stop_price is not None and low <= prev_stop_price and volume > 0
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = SELL, "exit_stop", True

        # 5. Roll snapshots / history, then advance counters.
        self._prev_condition1 = condition1
        self._prev_close = close
        self._prev_atr = atr
        self._highs.append(high)
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
