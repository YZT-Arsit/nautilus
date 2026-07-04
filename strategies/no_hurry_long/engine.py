"""No Hurry long — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``NoHurrySystem_L`` system — the long mirror of
``no_hurry_short``:

* ``UpperChan = Highest(High, ChanLength)``; ``LowerChan = Lowest(Low,
  ChanLength)`` — a rolling high/low channel;
* the channel is read **shifted back** by ``ChanDelay + 1`` bars — i.e. the value
  ``UpperChan[ChanDelay+1]`` / ``LowerChan[ChanDelay+1]``;
* entry (long): flat and ``High >= UpperChan[ChanDelay+1]`` while ``High[1] <
  UpperChan[ChanDelay+1]`` (this bar first breaks the shifted upper channel) ->
  long at ``Max(Open, UpperChan[ChanDelay+1])``;
* ``PosHigh`` tracks the highest high since entry (``= High`` on the entry bar,
  then a running max);
* ``ATRVal = AvgTrueRange(ATRLength) * TrailingATRs``;
* exit (flatten), once ``BarsSinceEntry > 0``: ``stopline = Max(PosHigh[1] -
  ATRVal[1], LowerChan[ChanDelay+1] - tick)`` and ``Low <= stopline`` -> sell at
  ``Min(Open, stopline)``.

Faithful TradeBlazer semantics preserved: the breakout compares this bar's
``High`` and ``High[1]`` against the **same** shifted channel value; the trailing
stop reads ``PosHigh[1]`` / ``ATRVal[1]`` (previous-bar values) but the shifted
lower channel at the current bar; ``MarketPosition == 0`` / ``== 1`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0`` (so entry and
sell never fire on the same bar). ``AvgTrueRange`` is a simple mean of true range
over ``atr_length`` (matches ``no_hurry_short`` / ``trend_breakout_atr``; the
TradeBlazer builtin uses Wilder smoothing). There is **no** ``Vol > 0`` gate.
"""
from __future__ import annotations

from collections import deque

from strategies.no_hurry_long.config import NoHurryLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class NoHurryLongEngine:
    """Pure, position-aware No Hurry long engine."""

    def __init__(self, config: NoHurryLongConfig) -> None:
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
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.pos_high: float | None = None  # PosHigh: highest high since entry

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_high: float | None = None
        self._prev_pos_high: float | None = None
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
        full = len(self._upper_hist) == cfg.chan_delay + 2
        shifted_upper = self._upper_hist[0] if full else None
        shifted_lower = self._lower_hist[0] if full else None

        # 2. ATR (simple mean of true range) * TrailingATRs.
        tr = high - low if self._tr_prev_close is None else max(
            high - low, abs(high - self._tr_prev_close), abs(low - self._tr_prev_close)
        )
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None
        atr_val = atr * cfg.trailing_atrs if atr is not None else None

        mp_start = self.position

        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open long): this bar first breaks the shifted upper channel.
        con = (
            shifted_upper is not None and self._prev_high is not None
            and high >= shifted_upper and self._prev_high < shifted_upper
        )
        if not acted and mp_start == 0 and con:
            entry_price = max(open_, shifted_upper)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.pos_high = high                     # BarsSinceEntry == 0 -> PosHigh = High
            signal, reason, acted = BUY, "enter_long", True
        elif mp_start == 1:
            # PosHigh running max while long (High > PosHigh[1] -> PosHigh = High).
            if self.pos_high is None or high > self.pos_high:
                self.pos_high = high

        # 4. EXIT (flatten): trailing ATR stop / shifted-channel stop.
        if not acted and mp_start == 1 and self.bars_since_entry > 0:
            if (
                self._prev_pos_high is not None and self._prev_atr_val is not None
                and shifted_lower is not None
            ):
                stopline = max(self._prev_pos_high - self._prev_atr_val, shifted_lower - cfg.tick)
                if low <= stopline:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = SELL, "exit_stop", True

        # 5. Roll the prev-bar snapshots, then advance counters.
        self._prev_high = high
        self._prev_pos_high = self.pos_high
        self._prev_atr_val = atr_val
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
