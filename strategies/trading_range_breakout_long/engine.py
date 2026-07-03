"""Trading Range Breakout long — pure decision engine (position-aware, offline).

Long-side mirror of ``strategies/trading_range_breakout_short/engine.py``. Holds
**only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Trading_Range_Breakout_L`` system:

* ``RangeH/RangeL`` = highest high / lowest low of the prior ``range_len`` bars;
* ``NoTrades`` = summed "empty space" inside the range (large in a quiet range);
* setup (previous bar): ``Condition1`` ``NoTrades >= TRange * rng_pcnt/100`` AND
  ``Condition2`` ``TrueRange > ATRMA[1]`` (volatility expansion);
* entry (long): setup AND ``Condition3`` (``Close > RangeH`` and bar mid-price >
  ``High[1]``) on the previous bar, flat, ``Vol > 0`` -> buy at Open; record the
  initial stop ``LongRisk = RangeL`` and the profit high ``LongHigh = High``;
* exits (priority order): a bearish-reversal exit ``Condition4[1]`` (``Close <
  RangeL`` and mid < ``Low[1]``); the initial stop ``Low <= LongRisk``; the ATR
  trailing stop ``Low <= LongHigh[1] - atr_s * ATR[1]``.

``[1]`` semantics preserved; the exit is gated by ``BarsSinceEntry > 0`` so an
entry bar never exits. Fidelity notes mirror the short engine (simple-mean ATR;
Open / Min(Open, level) fills on the string-signal path).
"""
from __future__ import annotations

from collections import deque

from strategies.trading_range_breakout_long.config import TradingRangeBreakoutLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class TradingRangeBreakoutLongEngine:
    """Pure, position-aware Trading Range Breakout long engine."""

    def __init__(self, config: TradingRangeBreakoutLongConfig) -> None:
        self.cfg = config
        self._highs: deque[float] = deque(maxlen=config.range_len)
        self._lows: deque[float] = deque(maxlen=config.range_len)
        self._trs: deque[float] = deque(maxlen=config.range_len)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.long_risk: float | None = None    # initial stop (RangeL at entry)
        self.long_high: float | None = None     # profit high since entry
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_atr: float | None = None
        self._prev_atrma: float | None = None
        self._prev_long_high: float | None = None
        self._prev_cond1 = False
        self._prev_cond2 = False
        self._prev_cond3 = False
        self._prev_cond4 = False

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1
        n = cfg.range_len

        # 1. Range + gap-sum from the PRIOR range_len bars (before appending).
        if len(self._highs) == n:
            range_h = max(self._highs)
            range_l = min(self._lows)
            trange = range_h - range_l
            no_trades = sum(range_h - hi for hi in self._highs) + sum(lo - range_l for lo in self._lows)
        else:
            range_h = range_l = trange = no_trades = None

        # 2. True range + ATR(atr_len) and ATRMA(range_len).
        if self._tr_prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._tr_prev_close), abs(low - self._tr_prev_close))
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = (sum(list(self._trs)[-cfg.atr_len:]) / cfg.atr_len
               if len(self._trs) >= cfg.atr_len else None)
        atrma = sum(self._trs) / len(self._trs) if len(self._trs) == n else None

        # 3. Conditions on the CURRENT bar (identical to the short engine).
        mid = (high + low) * 0.5
        cond1 = (no_trades is not None and no_trades >= trange * (cfg.rng_pcnt * 0.01))
        cond2 = (self._prev_atrma is not None and tr > self._prev_atrma)
        cond3 = (range_h is not None and close > range_h
                 and self._prev_high is not None and mid > self._prev_high)
        cond4 = (range_l is not None and close < range_l
                 and self._prev_low is not None and mid < self._prev_low)

        signal, reason = HOLD, "hold"
        entered = False

        # 4. ENTRY (open long): previous bar's setup + upside breakout.
        if (
            self.position == 0
            and self._prev_cond1
            and self._prev_cond2
            and self._prev_cond3
            and volume > 0
            and range_l is not None
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.long_risk = range_l    # initial stop = current bar's RangeL
            self.long_high = high       # profit high starts at current bar's High
            self.last_entry_price = open_
            signal, reason = BUY, "enter_long"
            entered = True

        # 5. Update the profit high (in position, after the entry bar).
        if self.position == 1 and self.bars_since_entry > 0 and not entered and self.long_high is not None:
            self.long_high = max(self.long_high, high)

        # 6. EXITS (priority: bearish reversal -> initial stop -> ATR trailing).
        if self.position == 1 and self.bars_since_entry > 0 and volume > 0 and not entered:
            if self._prev_cond4:
                signal, reason = SELL, "exit_reversal"
                self._flat()
            elif self.long_risk is not None and low <= self.long_risk:
                signal, reason = SELL, "exit_initial_stop"
                self._flat()
            elif (
                self._prev_long_high is not None
                and self._prev_atr is not None
                and low <= self._prev_long_high - cfg.atr_s * self._prev_atr
            ):
                signal, reason = SELL, "exit_trailing_stop"
                self._flat()

        # 7. Roll buffers + save the ``[1]`` snapshots, then advance counters.
        self._highs.append(high)
        self._lows.append(low)
        self._prev_high = high
        self._prev_low = low
        self._prev_atr = atr
        self._prev_atrma = atrma
        self._prev_long_high = self.long_high
        self._prev_cond1, self._prev_cond2 = cond1, cond2
        self._prev_cond3, self._prev_cond4 = cond3, cond4
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.long_risk = None
        self.long_high = None
        self.last_entry_price = None
