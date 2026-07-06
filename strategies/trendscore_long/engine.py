"""TrendScore long — pure decision engine (position-aware, offline-testable).

Long-side mirror of ``strategies/trendscore_short/engine.py``. Holds **only** the
signal-decision maths (plain-Python rolling windows; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD``; the signal->order meaning is ``sell_means: flat``
(BUY opens the long, SELL flattens it), like ``vwm_long``/``ma_crossover``.
Single unit, no pyramiding.

Ported from the TradeBlazer ``TrendScore_L`` system:

* ``TrendScore`` = sum over ``i in 1..look_back`` of ``+1 if C >= C[i] else -1``;
* ``MA`` = Average(Close, ma_length); ``TrendScoreMA`` = Average(TrendScore, ma_length);
* entry (long) when flat and ``Close[1] >= MA[1]`` and ``TrendScore[1] >=
  TrendScoreMA[1]`` and ``Vol > 0``;
* exit is an ATR stop stack — protective (``Low[1] - k*ATR[1]`` fixed at entry),
  trailing (``HighAfterEntry[1] - k*ATR[1]``) and break-even (``LastEntryPrice``,
  armed once the trade is ``breakeven_stop_atr_multi * ATR`` in profit); the exit
  line is the **max** (tightest) of the applicable stops and fires when the low
  trades through it.

``[1]`` semantics are preserved: all entry comparisons and every stop level use
the **previous** bar's MA / score / ATR / low / HighAfterEntry / ProtectStop, and
the exit is gated by ``MarketPosition == 1 And mp[1] == 1`` (long for at least one
full bar), so an entry bar never exits.

Fidelity notes mirror the short engine: ``AvgTrueRange`` is a simple mean of true
range over ``atr_length`` (Nautilus-free, offline-testable); the entry books
``last_entry_price = Open`` internally for faithful break-even maths, while the
shared string-signal path fills at the signal bar (same limitation as vwm_long).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range

from strategies.trendscore_long.config import TrendScoreLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class TrendScoreLongEngine:
    """Pure, position-aware TrendScore long engine (no Nautilus, no pandas)."""

    def __init__(self, config: TrendScoreLongConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.look_back)     # prior closes for scoring
        self._ma_closes: deque[float] = deque(maxlen=config.ma_length)  # closes for MA
        self._scores: deque[float] = deque(maxlen=config.ma_length)     # scores for TrendScoreMA
        self._trs: deque[float] = deque(maxlen=config.atr_length)       # true ranges for ATR
        self._prev_close: float | None = None

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only system)
        self.bars_since_entry = 0
        self.high_after_entry: float | None = None
        self.protect_stop: float | None = None
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_ma: float | None = None
        self._prev_score_ma: float | None = None
        self._prev_atr: float | None = None
        self._prev_close_val: float | None = None
        self._prev_score: float | None = None
        self._prev_low: float | None = None
        self._prev_high_after_entry: float | None = None
        self._prev_protect_stop: float | None = None
        self._prev_mp = 0

    # -- indicators ----------------------------------------------------------

    def _trend_score(self, close: float) -> float:
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

        # 2. ENTRY (open long) — flat, MA[1] ready & non-zero, prior close/score
        #    above their prior MAs, and volume present.
        if (
            self.position != 1
            and self._prev_ma is not None
            and self._prev_ma != 0
            and self._prev_atr is not None
            and self._prev_score_ma is not None
            and self._prev_close_val is not None
            and self._prev_score is not None
            and volume > 0
            and self._prev_close_val >= self._prev_ma
            and self._prev_score >= self._prev_score_ma
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.last_entry_price = open_  # TradeBlazer enters at Open
            self.protect_stop = self._prev_low - cfg.protect_stop_atr_multi * self._prev_atr
            self.high_after_entry = high   # BarsSinceEntry == 0 -> HighAfterEntry = High
            signal, reason = BUY, "enter_long"
            entered = True

        # 3. Maintain the post-entry high every in-position bar.
        if self.position == 1 and not entered:
            self.high_after_entry = (
                high if self.high_after_entry is None else max(self.high_after_entry, high)
            )

        # 4. EXIT (sell) — only when long for at least one full bar (mp[1] == 1).
        if (
            self.position == 1
            and self._prev_mp == 1
            and not entered
            and self._prev_atr is not None
            and self._prev_high_after_entry is not None
            and self._prev_protect_stop is not None
            and self.last_entry_price is not None
        ):
            trail_stop = self._prev_high_after_entry - cfg.trail_stop_atr_multi * self._prev_atr
            breakeven_stop = self.last_entry_price
            if self._prev_high_after_entry >= breakeven_stop + cfg.breakeven_stop_atr_multi * self._prev_atr:
                exit_line = trail_stop if trail_stop >= breakeven_stop else breakeven_stop
            else:
                exit_line = trail_stop if trail_stop >= self._prev_protect_stop else self._prev_protect_stop
            if low <= exit_line and volume > 0:
                signal, reason = SELL, "exit_long_stop"
                self.position = 0
                self.bars_since_entry = 0
                self.high_after_entry = None
                self.protect_stop = None
                self.last_entry_price = None

        # 5. Save this bar's values as the ``[1]`` snapshots for the next bar,
        #    then advance bars-since-entry.
        self._prev_ma = ma
        self._prev_score_ma = score_ma
        self._prev_atr = atr
        self._prev_close_val = close
        self._prev_score = score
        self._prev_low = low
        self._prev_high_after_entry = self.high_after_entry
        self._prev_protect_stop = self.protect_stop
        self._prev_mp = self.position
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
