"""TrendScore short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths. Imports nothing from ``feature_engine``,
``strategy_framework``, ``nautilus_trader`` or ``pandas`` — plain-Python rolling
windows, so the engine is unit-testable offline. Same structural pattern as
``trend_breakout_atr`` / ``vwm_short``: a position-aware engine that emits
``BUY``/``SELL``/``HOLD``, with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — ``SELL`` opens the short, ``BUY``
covers it). Single unit, no pyramiding.

Ported from the TradeBlazer ``TrendScore_S`` system:

* ``TrendScore`` = sum over ``i in 1..look_back`` of ``+1 if C >= C[i] else -1``
  (score of the current close vs each of the prior ``look_back`` closes);
* ``MA`` = Average(Close, ma_length); ``TrendScoreMA`` = Average(TrendScore, ma_length);
* entry (short) when flat and ``Close[1] <= MA[1]`` and ``TrendScore[1] <=
  TrendScoreMA[1]`` and ``Vol > 0``;
* exit is an ATR stop stack — protective (``High[1] + k*ATR[1]`` fixed at entry),
  trailing (``LowAfterEntry[1] + k*ATR[1]``) and break-even (``LastEntryPrice``,
  armed once the trade is ``breakeven_stop_atr_multi * ATR`` in profit).

``[1]`` semantics are preserved: all entry comparisons and every stop level use
the **previous** bar's MA / score / ATR / high / LowAfterEntry / ProtectStop, and
the exit is gated by ``MarketPosition == -1 And mp[1] == -1`` (short for at least
one full bar), so an entry bar never exits.

Fidelity notes:

* ``AvgTrueRange`` is approximated as a simple mean of true range over
  ``atr_length`` (matches ``trend_breakout_atr``; the TradeBlazer builtin uses
  Wilder smoothing) — keeps the engine Nautilus-free and offline-testable.
* TradeBlazer fills the entry at ``Open`` and the exit at ``Max(Open, ExitLine)``.
  On the shared string-signal path the fill is a market fill at the signal bar
  (same accepted limitation as ``vwm_short``). The engine still books its own
  ``last_entry_price = Open`` internally so the break-even maths stay faithful.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range

from strategies.trendscore_short.config import TrendScoreShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class TrendScoreShortEngine:
    """Pure, position-aware TrendScore short engine (no Nautilus, no pandas).

    Feed one completed bar at a time via :meth:`update`; returns ``(signal,
    reason)``. Tracks position (flat / short), bars-since-entry, the post-entry
    low, the ATR stop levels, and rolling buffers for close / score / true range.
    """

    def __init__(self, config: TrendScoreShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.look_back)     # prior closes for scoring
        self._ma_closes: deque[float] = deque(maxlen=config.ma_length)  # closes for MA
        self._scores: deque[float] = deque(maxlen=config.ma_length)     # scores for TrendScoreMA
        self._trs: deque[float] = deque(maxlen=config.atr_length)       # true ranges for ATR
        self._prev_close: float | None = None

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only system)
        self.bars_since_entry = 0
        self.low_after_entry: float | None = None
        self.protect_stop: float | None = None
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_ma: float | None = None
        self._prev_score_ma: float | None = None
        self._prev_atr: float | None = None
        self._prev_close_val: float | None = None
        self._prev_score: float | None = None
        self._prev_high: float | None = None
        self._prev_low_after_entry: float | None = None
        self._prev_protect_stop: float | None = None
        self._prev_mp = 0

    # -- indicators ----------------------------------------------------------

    def _trend_score(self, close: float) -> float:
        """+1/-1 for the current close vs each prior close in the buffer."""
        priors = list(self._closes)  # up to look_back prior closes (current not yet appended)
        return float(sum(1 if close >= pc else -1 for pc in priors))

    # -- main step -----------------------------------------------------------

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg

        # 1. current-bar indicators (score uses prior closes; MA/ATR include current).
        score = self._trend_score(close)
        self._closes.append(close)
        self._ma_closes.append(close)
        self._scores.append(score)
        self._trs.append(true_range(high, low, self._prev_close))
        self._prev_close = close

        ma = sum(self._ma_closes) / len(self._ma_closes) if len(self._ma_closes) == cfg.ma_length else None
        score_ma = sum(self._scores) / len(self._scores) if len(self._scores) == cfg.ma_length else None
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None

        signal, reason = HOLD, "hold"
        entered = False

        # 2. ENTRY (open short) — flat, MA[1] ready & non-zero, prior close/score
        #    below their prior MAs, and volume present.
        if (
            self.position != -1
            and self._prev_ma is not None
            and self._prev_ma != 0
            and self._prev_atr is not None
            and self._prev_score_ma is not None
            and self._prev_close_val is not None
            and self._prev_score is not None
            and volume > 0
            and self._prev_close_val <= self._prev_ma
            and self._prev_score <= self._prev_score_ma
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.last_entry_price = open_  # TradeBlazer enters at Open
            self.protect_stop = self._prev_high + cfg.protect_stop_atr_multi * self._prev_atr
            self.low_after_entry = low     # BarsSinceEntry == 0 -> LowAfterEntry = Low
            signal, reason = SELL, "enter_short"
            entered = True

        # 3. Maintain the post-entry low every in-position bar.
        if self.position == -1 and not entered:
            self.low_after_entry = (
                low if self.low_after_entry is None else min(self.low_after_entry, low)
            )

        # 4. EXIT (cover) — only when short for at least one full bar (mp[1] == -1).
        if (
            self.position == -1
            and self._prev_mp == -1
            and not entered
            and self._prev_atr is not None
            and self._prev_low_after_entry is not None
            and self._prev_protect_stop is not None
            and self.last_entry_price is not None
        ):
            trail_stop = self._prev_low_after_entry + cfg.trail_stop_atr_multi * self._prev_atr
            breakeven_stop = self.last_entry_price
            if self._prev_low_after_entry <= breakeven_stop - cfg.breakeven_stop_atr_multi * self._prev_atr:
                exit_line = trail_stop if trail_stop <= breakeven_stop else breakeven_stop
            else:
                exit_line = trail_stop if trail_stop <= self._prev_protect_stop else self._prev_protect_stop
            if high >= exit_line and volume > 0:
                signal, reason = BUY, "exit_short_stop"
                self.position = 0
                self.bars_since_entry = 0
                self.low_after_entry = None
                self.protect_stop = None
                self.last_entry_price = None

        # 5. Save this bar's values as the ``[1]`` snapshots for the next bar,
        #    then advance bars-since-entry.
        self._prev_ma = ma
        self._prev_score_ma = score_ma
        self._prev_atr = atr
        self._prev_close_val = close
        self._prev_score = score
        self._prev_high = high
        self._prev_low_after_entry = self.low_after_entry
        self._prev_protect_stop = self.protect_stop
        self._prev_mp = self.position
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
