"""MA-Crossover Channel-Breakout long — pure decision engine (position-aware).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``MovingAverageCrossOver_L`` system (long-only):

* ``FastMA = Average(Close, FastLen)``; ``SlowMA = Average(Close, SlowLen)``;
* a **golden cross** (``CrossOver(FastMA, SlowMA)``) records the long breakout
  price ``LEntryPrice = Highest(High, ChLen) * (1 + ExtraPercentage/1e4)`` and
  stamps ``LCount = CurrentBar``;
* initial entry: flat, within ``(LCount, LCount + ChLen]`` bars, ``High >=
  LEntryPrice`` and ``Vol > 0`` -> long; disable further initial entries
  (``LCount = -999``);
* a **death cross** (``CrossUnder(FastMA, SlowMA)``) records the reverse price
  ``SEntryPrice = Lowest(Low, ChLen) * (1 - ExtraPercentage/1e4)`` and stamps
  ``SCount``; within ``(SCount, SCount + ChLen]`` a ``Low <= SEntryPrice`` break
  closes any long (trend reversal) and disables initial entry / re-entry;
* trailing stop: while long (``BarsSinceEntry > 0``) a ``Low <= Lowest(Low[1],
  TrailBar)`` break exits, stamps ``ReEntryCount = CurrentBar`` and arms
  ``ReEntryPrice = Highest(High, ReEntryChLen)``;
* re-entry: flat, ``BarsSinceExit > 0``, within ``ReEntryCount + ReBars`` bars,
  ``High >= ReEntryPrice`` and ``Vol > 0`` -> long again.

Faithful TradeBlazer semantics preserved: the counters (``LCount`` / ``SCount`` /
``ReEntryCount``) gate the entry / reverse / re-entry windows exactly as in the
source, the ``-999`` sentinel disables a window, and ``LEntryPrice`` /
``SEntryPrice`` / ``ReEntryPrice`` / ``TrailStopPrice`` persist as running state.
``MarketPosition`` uses the bar-start position and the exits are gated so entry
and exit never fire on one bar; ``BarsSinceExit > 0`` keeps re-entry off the exit
bar. The counters are initialised to the ``-999`` sentinel (rather than 0) so no
window is open until a real cross establishes it. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from strategies.ma_crossover_channel_long.config import MaCrossoverChannelLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_DISABLED = -999


class MaCrossoverChannelLongEngine:
    """Pure, position-aware MA-Crossover Channel-Breakout long engine."""

    def __init__(self, config: MaCrossoverChannelLongConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.slow_len)
        self._ch_highs: deque[float] = deque(maxlen=config.ch_len)
        self._ch_lows: deque[float] = deque(maxlen=config.ch_len)
        self._re_highs: deque[float] = deque(maxlen=config.re_entry_ch_len)
        self._trail_lows: deque[float] = deque(maxlen=config.trail_bar)  # prior lows (Low[1..TrailBar])

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.bars_since_exit = 0
        self.entry_price: float | None = None

        # persistent series (carry forward across bars)
        self.l_entry_price = 0.0
        self.s_entry_price = 0.0
        self.re_entry_price = 0.0
        self.l_count = _DISABLED
        self.s_count = _DISABLED
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
        self._re_highs.append(high)
        hh_ch = max(self._ch_highs)
        ll_ch = min(self._ch_lows)
        hh_re = max(self._re_highs)

        # 3. Trailing stop = Lowest(Low[1], TrailBar) — prior lows, excluding this bar.
        trail_stop_price = min(self._trail_lows) if self._trail_lows else None

        # 4. MA crosses.
        cross_over = cross_under = False
        if self._prev_fast is not None and self._prev_slow is not None and fast_ma is not None and slow_ma is not None:
            cross_over = self._prev_fast <= self._prev_slow and fast_ma > slow_ma
            cross_under = self._prev_fast >= self._prev_slow and fast_ma < slow_ma

        buffer = cfg.extra_percentage * 0.0001

        # 5. Golden cross -> arm the long breakout.
        if cross_over and cb >= cfg.ch_len - 1:
            self.l_entry_price = hh_ch * (1 + buffer)
            self.l_count = cb

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 6. INITIAL ENTRY (long): breakout within the post-golden-cross window.
        if (
            not acted and mp_start == 0
            and cb > self.l_count and cb <= self.l_count + cfg.ch_len
            and self.l_entry_price != 0 and high >= self.l_entry_price and volume > 0
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = max(open_, self.l_entry_price)
            self.l_count = _DISABLED          # only one initial entry per golden cross
            signal, reason, acted = BUY, "enter_long", True

        # 7. Death cross -> arm the reverse (trend-over) price.
        if cross_under and cb >= cfg.ch_len - 1:
            self.s_entry_price = ll_ch * (1 - buffer)
            self.s_count = cb

        # 8. REVERSE BREAKOUT: close the long on a downward break; disable windows.
        reverse_break = (
            cb > self.s_count and cb <= self.s_count + cfg.ch_len
            and self.s_entry_price != 0 and low <= self.s_entry_price and volume > 0
        )
        if reverse_break:
            if not acted and mp_start == 1:
                self.position = 0
                self.bars_since_entry = 0
                self.bars_since_exit = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_reverse", True
            self.l_count = _DISABLED
            self.re_entry_count = _DISABLED

        # 9. TRAILING STOP: periodic-low break exits and arms a re-entry.
        if (
            not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0
            and trail_stop_price is not None and low <= trail_stop_price
        ):
            self.position = 0
            self.bars_since_entry = 0
            self.bars_since_exit = 0
            self.entry_price = None
            self.re_entry_count = cb
            self.re_entry_price = hh_re
            signal, reason, acted = SELL, "exit_trail_stop", True

        # 10. RE-ENTRY (long): breakout of the re-entry price within the window.
        if (
            not acted and mp_start == 0 and self.bars_since_exit > 0
            and cb <= self.re_entry_count + cfg.re_bars
            and self.re_entry_price != 0 and high >= self.re_entry_price and volume > 0
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = max(open_, self.re_entry_price)
            signal, reason, acted = BUY, "enter_reentry", True

        # 11. Roll snapshots / history, then advance counters.
        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        self._trail_lows.append(low)       # becomes Low[1..] for the next bar
        if self.position == 1:
            self.bars_since_entry += 1
        else:
            self.bars_since_exit += 1

        return signal, reason
