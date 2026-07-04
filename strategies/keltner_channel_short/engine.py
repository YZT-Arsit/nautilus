"""Keltner Channel short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``KeltnerChannel_S`` system:

* ``AvgVal = Average(Close, length)``; ``AvgRange = Average(TrueRange, length)``;
* ``KCU = AvgVal + AvgRange*Constt``; ``KCL = AvgVal - AvgRange*Constt``;
  ``ChanRng = (KCU - KCL)/2`` (== ``AvgRange*Constt``);
* ``CountS`` increments every bar; a **down-cross** ``con = CrossUnder(Close,
  KCL)`` resets ``CountS = 0``, stamps ``SetBar = Low`` and arms the trigger
  ``ll = Low - ChanRng*ChanPcnt``;
* entry (short): flat, ``Close[1] < KCL[1]``, ``CountS <= sellN`` and ``Low <=
  ll`` -> short at ``Min(Open, ll)``;
* exit (cover), once ``BarsSinceEntry > 0``: ``con2[1]`` (``CrossOver(Close,
  AvgVal)`` on the previous bar) -> cover at ``Open``; or ``High >=
  Highest(High[1], stopN)`` -> cover at ``Max(Sstopline, Open)``.

Faithful TradeBlazer semantics preserved: the entry reads ``Close[1]`` / ``KCL[1]``
and the exit reads ``con2[1]`` (previous-bar values); ``ll`` / ``SetBar`` /
``CountS`` persist as running state (the trigger is re-armed only on a fresh
down-cross); ``Sstopline = Highest(High[1], stopN)`` excludes the current bar;
``MarketPosition`` uses the bar-start position and the exit is gated by
``BarsSinceEntry > 0``, so entry and cover never fire on one bar. There is **no**
``Vol > 0`` gate in the source. ``Average`` / the ATR are simple means (the
TradeBlazer ATR builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from strategies.keltner_channel_short.config import KeltnerChannelShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class KeltnerChannelShortEngine:
    """Pure, position-aware Keltner Channel short engine."""

    def __init__(self, config: KeltnerChannelShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.length)
        self._trs: deque[float] = deque(maxlen=config.length)
        self._tr_prev_close: float | None = None
        self._prior_highs: deque[float] = deque(maxlen=config.stop_n)  # High[1..stopN]

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent state
        self.count_s = 0
        self.ll: float | None = None       # short trigger price
        self.set_bar: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_kcl: float | None = None
        self._prev_avgval: float | None = None
        self._prev_con2 = False            # con2[1] (CrossOver on the previous bar)

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Channel: SMA(close) +/- Constt * ATR(length).
        self._closes.append(close)
        avgval = sum(self._closes) / len(self._closes) if len(self._closes) == cfg.length else None
        tr = high - low if self._tr_prev_close is None else max(
            high - low, abs(high - self._tr_prev_close), abs(low - self._tr_prev_close)
        )
        self._trs.append(tr)
        self._tr_prev_close = close
        avgrange = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.length else None

        kcl = None
        chan_rng = None
        if avgval is not None and avgrange is not None:
            kcl = avgval - avgrange * cfg.constt
            chan_rng = avgrange * cfg.constt      # (KCU - KCL) / 2

        # 2. CountS increments each bar; a down-cross re-arms the trigger.
        self.count_s += 1
        con = False
        if self._prev_close is not None and self._prev_kcl is not None and kcl is not None:
            con = self._prev_close >= self._prev_kcl and close < kcl   # CrossUnder(Close, KCL)
        if con:
            self.set_bar = low
            self.count_s = 0
            self.ll = low - chan_rng * cfg.chan_pcnt

        # 3. Exit crossover + protective stop (computed before position updates).
        con2 = False
        if self._prev_close is not None and self._prev_avgval is not None and avgval is not None:
            con2 = self._prev_close <= self._prev_avgval and close > avgval   # CrossOver(Close, AvgVal)
        sstopline = max(self._prior_highs) if self._prior_highs else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open short): prior close below the lower band, within the
        #    trigger window, break of the trigger price.
        if (
            not acted and mp_start == 0
            and self._prev_close is not None and self._prev_kcl is not None
            and self._prev_close < self._prev_kcl
            and self.count_s <= cfg.sell_n
            and self.ll is not None and low <= self.ll
        ):
            entry_price = min(open_, self.ll)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 5. EXIT (cover): close back above the mid on the prior bar, else N-bar-high stop.
        if not acted and mp_start == -1 and self.bars_since_entry > 0:
            if self._prev_con2:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_mid_cross", True
            elif sstopline is not None and high >= sstopline:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_stop", True

        # 6. Roll snapshots / history, then advance counters.
        self._prev_close = close
        self._prev_kcl = kcl
        self._prev_avgval = avgval
        self._prev_con2 = con2
        self._prior_highs.append(high)     # becomes High[1..stopN] for the next bar
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
