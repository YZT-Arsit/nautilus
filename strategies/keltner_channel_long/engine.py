"""Keltner Channel long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``KeltnerChannel_L`` system — the long mirror of
``keltner_channel_short``:

* ``AvgVal = Average(Close, length)``; ``AvgRange = Average(TrueRange, length)``;
* ``KCU = AvgVal + AvgRange*Constt``; ``KCL = AvgVal - AvgRange*Constt``;
  ``ChanRng = (KCU - KCL)/2`` (== ``AvgRange*Constt``);
* ``CountL`` increments every bar; an **up-cross** ``con = CrossOver(Close, KCU)``
  resets ``CountL = 0``, stamps ``SetBar = High`` and arms the trigger
  ``hh = High + ChanRng*ChanPcnt``;
* entry (long): flat, ``Close[1] > KCU[1]``, ``CountL <= buyN`` and ``High >=
  hh`` -> long at ``Max(Open, hh)``;
* exit (flatten), once ``BarsSinceEntry > 0``: ``con2[1]`` (``CrossUnder(Close,
  AvgVal)`` on the previous bar) -> sell at ``Open``; or ``Low <= Lowest(Low[1],
  stopN)`` -> sell at ``Min(Open, Lstopline)``.

Faithful TradeBlazer semantics preserved: the entry reads ``Close[1]`` / ``KCU[1]``
and the exit reads ``con2[1]`` (previous-bar values); ``hh`` / ``SetBar`` /
``CountL`` persist as running state (the trigger is re-armed only on a fresh
up-cross); ``Lstopline = Lowest(Low[1], stopN)`` excludes the current bar;
``MarketPosition`` uses the bar-start position and the exit is gated by
``BarsSinceEntry > 0``, so entry and sell never fire on one bar. There is **no**
``Vol > 0`` gate in the source. ``Average`` / the ATR are simple means (the
TradeBlazer ATR builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range

from strategies.keltner_channel_long.config import KeltnerChannelLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class KeltnerChannelLongEngine:
    """Pure, position-aware Keltner Channel long engine."""

    def __init__(self, config: KeltnerChannelLongConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.length)
        self._trs: deque[float] = deque(maxlen=config.length)
        self._tr_prev_close: float | None = None
        self._prior_lows: deque[float] = deque(maxlen=config.stop_n)  # Low[1..stopN]

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent state
        self.count_l = 0
        self.hh: float | None = None       # long trigger price
        self.set_bar: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_kcu: float | None = None
        self._prev_avgval: float | None = None
        self._prev_con2 = False            # con2[1] (CrossUnder on the previous bar)

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Channel: SMA(close) +/- Constt * ATR(length).
        self._closes.append(close)
        avgval = sum(self._closes) / len(self._closes) if len(self._closes) == cfg.length else None
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        avgrange = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.length else None

        kcu = None
        chan_rng = None
        if avgval is not None and avgrange is not None:
            kcu = avgval + avgrange * cfg.constt
            chan_rng = avgrange * cfg.constt      # (KCU - KCL) / 2

        # 2. CountL increments each bar; an up-cross re-arms the trigger.
        self.count_l += 1
        con = False
        if self._prev_close is not None and self._prev_kcu is not None and kcu is not None:
            con = self._prev_close <= self._prev_kcu and close > kcu   # CrossOver(Close, KCU)
        if con:
            self.set_bar = high
            self.count_l = 0
            self.hh = high + chan_rng * cfg.chan_pcnt

        # 3. Exit crossunder + protective stop (computed before position updates).
        con2 = False
        if self._prev_close is not None and self._prev_avgval is not None and avgval is not None:
            con2 = self._prev_close >= self._prev_avgval and close < avgval   # CrossUnder(Close, AvgVal)
        lstopline = min(self._prior_lows) if self._prior_lows else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open long): prior close above the upper band, within the
        #    trigger window, break of the trigger price.
        if (
            not acted and mp_start == 0
            and self._prev_close is not None and self._prev_kcu is not None
            and self._prev_close > self._prev_kcu
            and self.count_l <= cfg.buy_n
            and self.hh is not None and high >= self.hh
        ):
            entry_price = max(open_, self.hh)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = BUY, "enter_long", True

        # 5. EXIT (flatten): close back below the mid on the prior bar, else N-bar-low stop.
        if not acted and mp_start == 1 and self.bars_since_entry > 0:
            if self._prev_con2:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_mid_cross", True
            elif lstopline is not None and low <= lstopline:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_stop", True

        # 6. Roll snapshots / history, then advance counters.
        self._prev_close = close
        self._prev_kcu = kcu
        self._prev_avgval = avgval
        self._prev_con2 = con2
        self._prior_lows.append(low)       # becomes Low[1..stopN] for the next bar
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
