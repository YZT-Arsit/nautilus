"""Escalator long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Escalator_L`` system (long mirror of ``Escalator_S``):

* ``MA_Fast = Average(Close, FastLength)``, ``MA_Slow = Average(Close, SlowLength)``
  (simple means) define the big-picture regime;
* two candlestick-pattern flags ``Condition1 = Close <= Low + 0.25*(High-Low)``
  (close near the low) and ``Condition2 = Close >= High - 0.25*(High-Low)`` (close
  near the high);
* channels ``HH = Highest(High, 2)`` (entry) and ``LL = Lowest(Low, RiskLength)``
  (stop);
* entry (long): flat, ``Condition1[2] And Condition2[1]`` (a near-low bar then a
  near-high bar), bullish regime ``Close[1] > MA_Fast[1] And Close[1] > MA_Slow[1]``,
  ``Vol > 0`` and a break ``High >= HH[1] + tick`` -> long at ``Max(Open, HH[1] +
  tick)``; the stop level ``LongRisk = LL[1] - tick`` is fixed at entry;
* exit (sell), once ``BarsSinceEntry > 0`` and ``Vol > 0``: profit target ``High >=
  EntryPrice + ProfitFactor*(EntryPrice - LongRisk)`` -> sell at ``Max(Open,
  target)``; else stop ``Low <= LongRisk`` -> sell at ``Min(Open, LongRisk)``.

Faithful TradeBlazer semantics preserved: the pattern reads ``Condition1[2]`` and
``Condition2[1]``, the regime reads ``Close[1]``/``MA_*[1]``, and the channels read
``HH[1]``/``LL[1]`` (all previous-bar values snapshotted before the roll);
``HH = Highest(High, 2)`` uses the fixed window 2 while ``LL`` uses ``RiskLength``;
``LongRisk`` is captured at the entry fill and held; ``MarketPosition`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0``, so entry and
sell never fire on one bar. There **is** a ``Vol > 0`` gate. Exit priority: profit
target then stop. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from strategies.escalator_long.config import EscalatorLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_HH_WINDOW = 2  # TradeBlazer hard-codes Highest(High, 2) for the entry channel.


class EscalatorLongEngine:
    """Pure, position-aware Escalator long engine."""

    def __init__(self, config: EscalatorLongConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.slow_length)
        chan = max(_HH_WINDOW, config.risk_length)
        self._lows: deque[float] = deque(maxlen=chan)
        self._highs: deque[float] = deque(maxlen=chan)

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.long_risk: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._cond1_p1 = False
        self._cond1_p2 = False
        self._cond2_p1 = False
        self._prev_close: float | None = None
        self._prev_ma_fast: float | None = None
        self._prev_ma_slow: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sum(list(self._closes)[-period:]) / period

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg

        # 1. Channels from the PREVIOUS bars (deques hold up to t-1 before append).
        hh_prev = max(list(self._highs)[-_HH_WINDOW:]) if len(self._highs) >= _HH_WINDOW else None
        ll_prev = (
            min(list(self._lows)[-cfg.risk_length:])
            if len(self._lows) >= cfg.risk_length else None
        )

        # 2. Moving averages (current bar inclusive).
        self._closes.append(close)
        ma_fast = self._sma(cfg.fast_length)
        ma_slow = self._sma(cfg.slow_length)

        # 3. Candlestick-pattern flags (current bar).
        myrange = high - low
        cond1 = close <= low + 0.25 * myrange
        cond2 = close >= high - 0.25 * myrange

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open long): near-low then near-high pattern, bullish regime,
        #    break of the 2-bar high channel.
        if (
            not acted and mp_start == 0 and volume > 0
            and self._cond1_p2 and self._cond2_p1
            and self._prev_close is not None and self._prev_ma_fast is not None
            and self._prev_ma_slow is not None
            and self._prev_close > self._prev_ma_fast and self._prev_close > self._prev_ma_slow
            and hh_prev is not None and ll_prev is not None
            and high >= hh_prev + cfg.tick
        ):
            entry_price = max(open_, hh_prev + cfg.tick)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.long_risk = ll_prev - cfg.tick
            signal, reason, acted = BUY, "enter_long", True

        # 5. EXIT (sell): profit target then stop.
        if (
            not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0
            and self.entry_price is not None and self.long_risk is not None
        ):
            target = self.entry_price + cfg.profit_factor * (self.entry_price - self.long_risk)
            if high >= target:
                self._sell()
                signal, reason, acted = SELL, "exit_profit_target", True
            elif low <= self.long_risk:
                self._sell()
                signal, reason, acted = SELL, "exit_stop", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._cond1_p2 = self._cond1_p1
        self._cond1_p1 = cond1
        self._cond2_p1 = cond2
        self._prev_close = close
        self._prev_ma_fast = ma_fast
        self._prev_ma_slow = ma_slow
        self._lows.append(low)
        self._highs.append(high)
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _sell(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
        self.long_risk = None
