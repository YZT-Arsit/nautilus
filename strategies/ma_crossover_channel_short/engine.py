"""MA-Crossover Channel-Breakout short — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``MovingAverageCrossOver_S`` system (short-only) — the
mirror of ``ma_crossover_channel_long``:

* ``FastMA = Average(Close, FastLen)``; ``SlowMA = Average(Close, SlowLen)``;
* a **death cross** (``CrossUnder(FastMA, SlowMA)``) records the short breakout
  price ``SEntryPrice = Lowest(Low, ChLen) * (1 - ExtraPercentage/1e4)`` and stamps
  ``SCount = CurrentBar``;
* initial entry: flat, within ``(SCount, SCount + ChLen]`` bars, ``Low <=
  SEntryPrice`` and ``Vol > 0`` -> short; disable further initial entries
  (``SCount = -999``);
* a **golden cross** (``CrossOver(FastMA, SlowMA)``) records the reverse price and
  stamps ``LCount``; within ``(LCount, LCount + ChLen]`` a ``High >= LEntryPrice``
  break covers any short (trend reversal) and disables initial entry / re-entry;
* trailing stop: while short (``BarsSinceEntry > 0``) a ``High >= Highest(High[1],
  TrailBar)`` break covers, stamps ``ReEntryCount = CurrentBar`` and arms
  ``ReEntryPrice = Lowest(Low, ReEntryChLen)``;
* re-entry: flat, ``BarsSinceExit > 0``, within ``ReEntryCount + ReBars`` bars,
  ``Low <= ReEntryPrice`` and ``Vol > 0`` -> short again.

**Source fidelity note (likely TradeBlazer typo, preserved verbatim):** the
reverse price in the source is ``LEntryPrice = Highest(High, ChLen) * (1 +
ExtraPercentage * 0.01)`` — a ``0.01`` factor, whereas every other buffer in this
system (and the long mirror) uses ``0.0001``. With the default
``ExtraPercentage = 300`` this makes the reverse line ``HH * 4`` (a +300% buffer),
so the reverse-breakout exit essentially never fires and the trailing stop is the
effective exit. This is kept exactly as written (``_REVERSE_MULT = 0.01``); flag
to the user if ``0.0001`` was intended.

Faithful TradeBlazer semantics preserved: the counters (``SCount`` / ``LCount`` /
``ReEntryCount``) gate the entry / reverse / re-entry windows exactly as in the
source, the ``-999`` sentinel disables a window (counters are initialised to it so
no window is open until a real cross establishes it), and the price series persist
as running state. ``MarketPosition`` uses the bar-start position and the exits are
gated so entry and cover never fire on one bar; ``BarsSinceExit > 0`` keeps
re-entry off the exit bar. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from strategies.ma_crossover_channel_short.config import MaCrossoverChannelShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_DISABLED = -999
_ENTRY_MULT = 0.0001    # ExtraPercentage * 0.0001 for the primary short breakout
_REVERSE_MULT = 0.01    # ExtraPercentage * 0.01 for the reverse line (TB source verbatim)


class MaCrossoverChannelShortEngine:
    """Pure, position-aware MA-Crossover Channel-Breakout short engine."""

    def __init__(self, config: MaCrossoverChannelShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.slow_len)
        self._ch_highs: deque[float] = deque(maxlen=config.ch_len)
        self._ch_lows: deque[float] = deque(maxlen=config.ch_len)
        self._re_lows: deque[float] = deque(maxlen=config.re_entry_ch_len)
        self._trail_highs: deque[float] = deque(maxlen=config.trail_bar)  # prior highs (High[1..TrailBar])

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.bars_since_exit = 0
        self.entry_price: float | None = None

        # persistent series (carry forward across bars)
        self.s_entry_price = 0.0
        self.l_entry_price = 0.0
        self.re_entry_price = 0.0
        self.s_count = _DISABLED
        self.l_count = _DISABLED
        self.re_entry_count = _DISABLED

        # previous-bar MA snapshots (for the crosses)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._bar += 1
        cb = self._bar

        # 1. Fast / slow moving averages (simple means).
        self._closes.append(close)
        n = len(self._closes)
        fast_ma = sum(list(self._closes)[-cfg.fast_len:]) / cfg.fast_len if n >= cfg.fast_len else None
        slow_ma = sum(self._closes) / cfg.slow_len if n >= cfg.slow_len else None

        # 2. Channel windows (include the current bar).
        self._ch_highs.append(high)
        self._ch_lows.append(low)
        self._re_lows.append(low)
        hh_ch = max(self._ch_highs)
        ll_ch = min(self._ch_lows)
        ll_re = min(self._re_lows)

        # 3. Trailing stop = Highest(High[1], TrailBar) — prior highs, excluding this bar.
        trail_stop_price = max(self._trail_highs) if self._trail_highs else None

        # 4. MA crosses.
        cross_over = cross_under = False
        if self._prev_fast is not None and self._prev_slow is not None and fast_ma is not None and slow_ma is not None:
            cross_over = self._prev_fast <= self._prev_slow and fast_ma > slow_ma
            cross_under = self._prev_fast >= self._prev_slow and fast_ma < slow_ma

        # 5. Death cross -> arm the short breakout.
        if cross_under and cb >= cfg.ch_len - 1:
            self.s_entry_price = ll_ch * (1 - cfg.extra_percentage * _ENTRY_MULT)
            self.s_count = cb

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 6. INITIAL ENTRY (short): breakout within the post-death-cross window.
        if (
            not acted and mp_start == 0
            and cb > self.s_count and cb <= self.s_count + cfg.ch_len
            and self.s_entry_price != 0 and low <= self.s_entry_price and volume > 0
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = min(open_, self.s_entry_price)
            self.s_count = _DISABLED          # only one initial entry per death cross
            signal, reason, acted = SELL, "enter_short", True

        # 7. Golden cross -> arm the reverse (trend-over) price. NOTE: TB source uses
        #    the 0.01 multiplier here (verbatim; see module docstring).
        if cross_over and cb >= cfg.ch_len - 1:
            self.l_entry_price = hh_ch * (1 + cfg.extra_percentage * _REVERSE_MULT)
            self.l_count = cb

        # 8. REVERSE BREAKOUT: cover the short on an upward break; disable windows.
        reverse_break = (
            cb > self.l_count and cb <= self.l_count + cfg.ch_len
            and self.l_entry_price != 0 and high >= self.l_entry_price and volume > 0
        )
        if reverse_break:
            if not acted and mp_start == -1:
                self.position = 0
                self.bars_since_entry = 0
                self.bars_since_exit = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_reverse", True
            self.s_count = _DISABLED
            self.re_entry_count = _DISABLED

        # 9. TRAILING STOP: periodic-high break covers and arms a re-entry.
        if (
            not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0
            and trail_stop_price is not None and high >= trail_stop_price
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.bars_since_exit = 0
            self.entry_price = None
            self.re_entry_count = cb
            self.re_entry_price = ll_re
            signal, reason, acted = BUY, "exit_trail_stop", True

        # 10. RE-ENTRY (short): breakdown of the re-entry price within the window.
        if (
            not acted and mp_start == 0 and self.bars_since_exit > 0
            and cb <= self.re_entry_count + cfg.re_bars
            and self.re_entry_price != 0 and low <= self.re_entry_price and volume > 0
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = min(open_, self.re_entry_price)
            signal, reason, acted = SELL, "enter_reentry", True

        # 11. Roll snapshots / history, then advance counters.
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        self._trail_highs.append(high)     # becomes High[1..] for the next bar
        if self.position == -1:
            self.bars_since_entry += 1
        else:
            self.bars_since_exit += 1

        return signal, reason
