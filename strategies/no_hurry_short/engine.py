"""No Hurry short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``NoHurrySystem_S`` system:

* ``UpperChan = Highest(High, ChanLength)``; ``LowerChan = Lowest(Low,
  ChanLength)`` — a rolling high/low channel;
* the channel is read **shifted back** by ``ChanDelay + 1`` bars — i.e. the value
  ``LowerChan[ChanDelay+1]`` / ``UpperChan[ChanDelay+1]``;
* entry (short): flat and ``Low <= LowerChan[ChanDelay+1]`` while ``Low[1] >
  LowerChan[ChanDelay+1]`` (this bar first breaks the shifted lower channel) ->
  short at ``Min(Open, LowerChan[ChanDelay+1])``;
* ``PosLow`` tracks the lowest low since entry (``= Low`` on the entry bar, then a
  running min);
* ``ATRVal = AvgTrueRange(ATRLength) * TrailingATRs``;
* exit (cover), once ``BarsSinceEntry > 0``: ``stopline = Min(PosLow[1] +
  ATRVal[1], UpperChan[ChanDelay+1] + tick)`` and ``High >= stopline`` -> cover at
  ``Max(Open, stopline)``.

Faithful TradeBlazer semantics preserved: the breakout compares this bar's ``Low``
and ``Low[1]`` against the **same** shifted channel value; the trailing stop reads
``PosLow[1]`` / ``ATRVal[1]`` (previous-bar values) but the shifted upper channel
at the current bar; ``MarketPosition == 0`` / ``== -1`` uses the bar-start
position and the exit is gated by ``BarsSinceEntry > 0`` (so entry and cover never
fire on the same bar). ``AvgTrueRange`` is a simple mean of true range over
``atr_length`` (matches ``trend_breakout_atr`` / ``redrover_*``; the TradeBlazer
builtin uses Wilder smoothing). There is **no** ``Vol > 0`` gate.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range
from strategies.no_hurry_short.config import NoHurryShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class NoHurryShortEngine:
    """Pure, position-aware No Hurry short engine."""

    def __init__(self, config: NoHurryShortConfig) -> None:
        self.cfg = config
        self._highs: deque[float] = deque(maxlen=config.chan_length)
        self._lows: deque[float] = deque(maxlen=config.chan_length)
        # Channel history: with maxlen = chan_delay + 2, index [0] is the value
        # ``chan_delay + 1`` bars ago once the deque is full.
        self._upper_hist: deque[float] = deque(maxlen=config.chan_delay + 2)
        self._lower_hist: deque[float] = deque(maxlen=config.chan_delay + 2)

        # True range for the simple-mean ATR.
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.pos_low: float | None = None  # PosLow: lowest low since entry

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_low: float | None = None
        self._prev_pos_low: float | None = None
        self._prev_atr_val: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Rolling high/low channel, then its shifted (delayed) read.
        self._highs.append(high)
        self._lows.append(low)
        upper_chan = max(self._highs)
        lower_chan = min(self._lows)
        self._upper_hist.append(upper_chan)
        self._lower_hist.append(lower_chan)
        full = len(self._lower_hist) == cfg.chan_delay + 2
        shifted_upper = self._upper_hist[0] if full else None
        shifted_lower = self._lower_hist[0] if full else None

        # 2. ATR (simple mean of true range) * TrailingATRs.
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None
        atr_val = atr * cfg.trailing_atrs if atr is not None else None

        mp_start = self.position

        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open short): this bar first breaks the shifted lower channel.
        con = (
            shifted_lower is not None and self._prev_low is not None
            and low <= shifted_lower and self._prev_low > shifted_lower
        )
        if not acted and mp_start == 0 and con:
            entry_price = min(open_, shifted_lower)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.pos_low = low                       # BarsSinceEntry == 0 -> PosLow = Low
            signal, reason, acted = SELL, "enter_short", True
        elif mp_start == -1:
            # PosLow running min while short (Low < PosLow[1] -> PosLow = Low).
            if self.pos_low is None or low < self.pos_low:
                self.pos_low = low

        # 4. EXIT (cover): trailing ATR stop / shifted-channel stop.
        if not acted and mp_start == -1 and self.bars_since_entry > 0:
            if (
                self._prev_pos_low is not None and self._prev_atr_val is not None
                and shifted_upper is not None
            ):
                stopline = min(self._prev_pos_low + self._prev_atr_val, shifted_upper + cfg.tick)
                if high >= stopline:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = BUY, "exit_stop", True

        # 5. Roll the prev-bar snapshots, then advance counters.
        self._prev_low = low
        self._prev_pos_low = self.pos_low
        self._prev_atr_val = atr_val
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
