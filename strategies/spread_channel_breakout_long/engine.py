"""Spread Channel Breakout long — pure decision engine (position-aware, offline).

Long-side mirror of ``strategies/spread_channel_breakout_short/engine.py``. Holds
**only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``SpreadChannelBreakout_L`` system. The strategy
trades the *spread* between two instruments; once the spread bar is formed the
logic is a plain channel breakout, which is exactly what this engine implements
on a single OHLC series (its open/close taken as the spread ``OO``/``CC``):

* ``HH = Max(OO, CC)``, ``LL = Min(OO, CC)`` — the spread bar's high/low come from
  its **open and close only** (the raw bar's high/low are ignored);
* ``UpperLine = Highest(HH[1], length)``, ``LowerLine = Lowest(LL[1], length)`` —
  channel from the HH/LL of the **prior** ``length`` bars;
* ``StopLine = Lowest(LL[1], stop_len)`` — the stop channel;
* entry (long): flat, ``CC[1] >= UpperLine[1]`` (spread breaks the upper channel),
  ``Vol > 0`` -> long at Open;
* reverse sell: long, ``CC[1] <= LowerLine[1]`` -> sell at Open;
* stop sell: long, ``BarsSinceEntry > 0`` and ``CC[1] <= StopLine[1]`` -> sell.

Faithful TradeBlazer semantics preserved (identical to the short engine, mirrored
to the long side): the channels use ``HH[1]``/``LL[1]`` (a one-bar shift) and every
decision reads the **previous** bar's CC / UpperLine / LowerLine / StopLine (a
second ``[1]``); ``MarketPosition`` is the **bar-start** position (``mp_start``):
entry tests ``== 0`` (flat), exits test ``== 1`` (long), so an entry and an exit
never both fill on one bar, and the stop is additionally gated by
``BarsSinceEntry > 0``. Exit priority: reverse channel first, then stop channel
(with ``length >= stop_len`` the stop channel is the higher/tighter of the two).

Two-leg note: see ``config.py`` — ``lots0``/``lots1`` and the leg contract
multipliers construct the spread in the two-data original; here the engine runs
the identical maths on one series fed as the spread.
"""
from __future__ import annotations

from collections import deque

from strategies.spread_channel_breakout_long.config import SpreadChannelBreakoutLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class SpreadChannelBreakoutLongEngine:
    """Pure, position-aware Spread Channel Breakout long engine."""

    def __init__(self, config: SpreadChannelBreakoutLongConfig) -> None:
        self.cfg = config
        span = max(config.length, config.stop_len)
        self._hh_hist: deque[float] = deque(maxlen=config.length)  # past HH (excludes current bar)
        self._ll_hist: deque[float] = deque(maxlen=span)           # past LL (LowerLine + StopLine)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0

        # previous-bar snapshots (the second ``[1]`` the decisions read)
        self._prev_cc: float | None = None
        self._prev_upper: float | None = None
        self._prev_lower: float | None = None
        self._prev_stopline: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Spread bar: HH/LL from open & close only (raw high/low ignored).
        oo, cc = open_, close
        hh = max(oo, cc)
        ll = min(oo, cc)

        # 2. Channels from the PRIOR bars' HH/LL (Highest(HH[1], N) etc.) — computed
        #    before appending the current HH/LL so the window excludes this bar.
        upper = max(list(self._hh_hist)[-cfg.length:]) if len(self._hh_hist) >= cfg.length else None
        lower = min(list(self._ll_hist)[-cfg.length:]) if len(self._ll_hist) >= cfg.length else None
        stopline = min(list(self._ll_hist)[-cfg.stop_len:]) if len(self._ll_hist) >= cfg.stop_len else None
        self._hh_hist.append(hh)
        self._ll_hist.append(ll)

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open long): spread close broke the prior upper channel.
        if (
            not acted and mp_start == 0
            and self._prev_cc is not None and self._prev_upper is not None
            and self._prev_cc >= self._prev_upper
            and volume > 0
        ):
            self.position = 1
            self.bars_since_entry = 0
            signal, reason, acted = BUY, "enter_long", True

        # 4. EXIT (sell): reverse channel first, then stop channel.
        if not acted and mp_start == 1 and volume > 0:
            if (
                self._prev_cc is not None and self._prev_lower is not None
                and self._prev_cc <= self._prev_lower
            ):
                self._flat()
                signal, reason, acted = SELL, "reverse_exit", True
            elif (
                self.bars_since_entry > 0
                and self._prev_cc is not None and self._prev_stopline is not None
                and self._prev_cc <= self._prev_stopline
            ):
                self._flat()
                signal, reason, acted = SELL, "stop_exit", True

        # 5. Roll the prev-bar snapshots, then advance counters.
        self._prev_cc = cc
        self._prev_upper = upper
        self._prev_lower = lower
        self._prev_stopline = stopline
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
