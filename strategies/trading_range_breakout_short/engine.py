"""Trading Range Breakout short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Same structural
pattern as ``vwm_short`` / ``trendscore_short``: a position-aware engine emitting
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short``). Single unit, no pyramiding.

Ported from the TradeBlazer ``Trading_Range_Breakout_S`` system:

* ``RangeH/RangeL`` = highest high / lowest low of the prior ``range_len`` bars;
  ``TRange = RangeH - RangeL``;
* ``NoTrades`` = sum over the prior ``range_len`` bars of ``(RangeH - High[i]) +
  (Low[i] - RangeL)`` — the total "empty space" inside the range (large in a
  quiet consolidation);
* setup (both on the previous bar): ``Condition1`` ``NoTrades >= TRange *
  rng_pcnt/100`` AND ``Condition2`` ``TrueRange > ATRMA[1]`` (a volatility
  expansion, ATRMA = ``range_len``-bar ATR);
* entry (short): setup AND ``Condition4`` (``Close < RangeL`` and bar mid-price <
  ``Low[1]``) on the previous bar, flat, ``Vol > 0`` -> sell at Open; record the
  initial stop ``ShortRisk = RangeH`` and the profit low ``ShortLow = Low``;
* exits (in priority order): a bullish-reversal exit ``Condition3[1]`` (``Close >
  RangeH`` and mid > ``High[1]``); the initial stop ``High >= ShortRisk``; the
  ATR trailing stop ``High >= ShortLow[1] + atr_s * ATR[1]``.

``[1]`` semantics preserved: the entry reads the previous bar's conditions; the
stops read the profit-low / ATR as of the previous bar; the exit is gated by
``BarsSinceEntry > 0`` so an entry bar never exits.

Fidelity note: ``AvgTrueRange`` is a simple mean of true range (matches the other
pure engines; TradeBlazer uses Wilder smoothing). Entry fills at ``Open`` and
stop exits at ``Max(Open, level)`` in TradeBlazer; on the string-signal path the
fill is a market fill at the signal bar (same limitation as ``vwm_short``).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range

from strategies.trading_range_breakout_short.config import TradingRangeBreakoutShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class TradingRangeBreakoutShortEngine:
    """Pure, position-aware Trading Range Breakout short engine."""

    def __init__(self, config: TradingRangeBreakoutShortConfig) -> None:
        self.cfg = config
        self._highs: deque[float] = deque(maxlen=config.range_len)  # prior bars' highs
        self._lows: deque[float] = deque(maxlen=config.range_len)   # prior bars' lows
        self._trs: deque[float] = deque(maxlen=config.range_len)    # true ranges (ATR & ATRMA)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.short_risk: float | None = None   # initial stop (RangeH at entry)
        self.short_low: float | None = None    # profit low since entry
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_atr: float | None = None      # ATR(atr_len)[1]
        self._prev_atrma: float | None = None    # ATRMA = ATR(range_len)[1]
        self._prev_short_low: float | None = None
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

        # 2. True range + ATR(atr_len) and ATRMA(range_len) (both include this bar).
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = (sum(list(self._trs)[-cfg.atr_len:]) / cfg.atr_len
               if len(self._trs) >= cfg.atr_len else None)
        atrma = sum(self._trs) / len(self._trs) if len(self._trs) == n else None

        # 3. Conditions on the CURRENT bar.
        mid = (high + low) * 0.5
        cond1 = (no_trades is not None and no_trades >= trange * (cfg.rng_pcnt * 0.01))
        cond2 = (self._prev_atrma is not None and tr > self._prev_atrma)  # TrueRange > ATRMA[1]
        cond3 = (range_h is not None and close > range_h
                 and self._prev_high is not None and mid > self._prev_high)
        cond4 = (range_l is not None and close < range_l
                 and self._prev_low is not None and mid < self._prev_low)

        signal, reason = HOLD, "hold"
        entered = False

        # 4. ENTRY (open short): previous bar's setup + breakout-down.
        if (
            self.position == 0
            and self._prev_cond1
            and self._prev_cond2
            and self._prev_cond4
            and volume > 0
            and range_h is not None
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.short_risk = range_h   # initial stop = current bar's RangeH
            self.short_low = low        # profit low starts at current bar's Low
            self.last_entry_price = open_
            signal, reason = SELL, "enter_short"
            entered = True

        # 5. Update the profit low (in position, after the entry bar).
        if self.position == -1 and self.bars_since_entry > 0 and not entered and self.short_low is not None:
            self.short_low = min(self.short_low, low)

        # 6. EXITS (priority: bullish reversal -> initial stop -> ATR trailing).
        if self.position == -1 and self.bars_since_entry > 0 and volume > 0 and not entered:
            if self._prev_cond3:
                signal, reason = BUY, "exit_reversal"
                self._flat()
            elif self.short_risk is not None and high >= self.short_risk:
                signal, reason = BUY, "exit_initial_stop"
                self._flat()
            elif (
                self._prev_short_low is not None
                and self._prev_atr is not None
                and high >= self._prev_short_low + cfg.atr_s * self._prev_atr
            ):
                signal, reason = BUY, "exit_trailing_stop"
                self._flat()

        # 7. Roll buffers + save the ``[1]`` snapshots, then advance counters.
        self._highs.append(high)
        self._lows.append(low)
        self._prev_high = high
        self._prev_low = low
        self._prev_atr = atr
        self._prev_atrma = atrma
        self._prev_short_low = self.short_low
        self._prev_cond1, self._prev_cond2 = cond1, cond2
        self._prev_cond3, self._prev_cond4 = cond3, cond4
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.short_risk = None
        self.short_low = None
        self.last_entry_price = None
