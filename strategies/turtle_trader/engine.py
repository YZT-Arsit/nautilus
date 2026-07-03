"""Turtle trading system — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths. Imports nothing from ``feature_engine``,
``strategy_framework.backends``, ``nautilus_trader`` or ``pandas`` — the indicator
maths are plain-Python rolling windows, so the engine is unit-testable offline.

Ported from the TradeBlazer ``TurtleTrader`` system. Because the framework
generates the entire signal stream before any fills occur, the engine is
**self-contained and position-aware**: it tracks its own position / units /
pyramid entries / stop reference, and emits a per-bar list of sized
:class:`TradeAction` s that a rich-plan-aware backend executes.

Faithful mappings
-----------------
* ``N`` = previous bar's ATR, ``AvgTR = XAverage(TrueRange, atr_length)`` (EMA);
* Donchian channels use the **prior** bars only (``HighestFC(High[1], len)``),
  so a bar never breaks out of its own range;
* short breakout (``breakout_len``) is gated by the last-profitable-trade filter
  (``PreBreakoutFailure``); the long failsafe breakout (``failsafe_len``) is not;
* pyramiding adds one ``TurtleUnits`` block per +0.5N move, capped at
  ``n_entries`` (open-gap add + a while-loop of adds within the bar);
* hard stop at ``entry -/+ 2N`` (skipped on a bar that added — "加仓Bar不止损");
* trailing exit at the ``trailing_exit_len`` Donchian channel closes everything.

Interpretation notes (documented departures where TradeBlazer is ambiguous on
bar-replayed data):

* ``MarketPosition`` is evaluated at the **start** of the bar; once an entry is
  emitted this bar the position is treated as no longer flat, so the short
  breakout and the failsafe breakout cannot double-open in the same bar.
* ``SendOrderThisBar`` resets each bar (its intended per-bar meaning): the hard
  stop is suppressed only on a bar that actually added.
"""
from __future__ import annotations

from collections import deque

from strategies.turtle_trader.config import TurtleTraderConfig
from strategy_framework.execution.intents import TradeAction

BUY, SELL, HOLD, EXIT = "BUY", "SELL", "HOLD", "EXIT"


class TurtleTraderEngine:
    """Pure, position-aware Turtle decision engine (no Nautilus, no pandas).

    Feed one completed bar at a time via :meth:`update`; returns
    ``(label, actions, reason)`` where ``actions`` is the sized order plan for the
    bar. Tracks position (flat/long/short), pyramid units/entries, the per-unit
    entry reference price, ATR (N), and the last-profitable-trade filter state.
    """

    def __init__(self, config: TurtleTraderConfig) -> None:
        self.cfg = config
        max_len = max(config.breakout_len, config.failsafe_len, config.trailing_exit_len)
        # Prior-bar high/low buffers (channels are computed BEFORE appending the
        # current bar, so they only ever see High[1..len] / Low[1..len]).
        self._highs: deque[float] = deque(maxlen=max_len)
        self._lows: deque[float] = deque(maxlen=max_len)
        self._prev_close: float | None = None

        # ATR (XAverage of TrueRange). ``_atr`` is the EMA value through the
        # previous bar; N = ``_atr`` (i.e. AvgTR[1]).
        self._atr: float | None = None
        self._atr_alpha = 2.0 / (config.atr_length + 1.0)

        self.current_bar = 0

        # Position state (the engine's own book; kept in sync with the fills it
        # requests, which the simulated backend executes at the same prices).
        self.position = 0            # -1 short, 0 flat, +1 long
        self.units = 0.0             # absolute units currently held
        self.current_entries = 0     # CurrentEntries: pyramided entries so far
        self.pre_entry_price: float | None = None  # preEntryPrice (last unit)
        self.pre_breakout_failure = False          # PreBreakoutFailure (LPT filter)

    # -- indicators ----------------------------------------------------------

    def _true_range(self, high: float, low: float) -> float:
        if self._prev_close is None:
            return high - low
        pc = self._prev_close
        return max(high - low, abs(high - pc), abs(low - pc))

    def _update_atr(self, true_range: float) -> None:
        if self._atr is None:
            self._atr = true_range  # seed the EMA on the first true range
        else:
            self._atr += self._atr_alpha * (true_range - self._atr)

    @staticmethod
    def _highest(buf: deque[float], length: int) -> float | None:
        if len(buf) < length:
            return None
        return max(list(buf)[-length:])

    @staticmethod
    def _lowest(buf: deque[float], length: int) -> float | None:
        if len(buf) < length:
            return None
        return min(list(buf)[-length:])

    def _turtle_units(self, n: float | None) -> float:
        """IntPart of the risk-based unit size; 0 when N is unavailable/non-positive."""
        if n is None or n <= 0:
            return 0.0
        cfg = self.cfg
        raw = (cfg.account_equity * cfg.risk_ratio / 100.0) / (
            n * cfg.contract_unit * cfg.big_point_value
        )
        units = float(int(raw))  # IntPart (truncate toward zero; raw >= 0 here)
        if cfg.max_units_per_entry is not None:
            units = min(units, float(int(cfg.max_units_per_entry)))
        return units

    # -- main step -----------------------------------------------------------

    def update(self, open_: float, high: float, low: float, close: float):
        """Process one completed bar; return ``(label, actions, reason)``."""
        cfg = self.cfg
        mp = cfg.min_point

        # 1. Channels from PRIOR bars only (computed before this bar is appended).
        donchian_hi = self._highest(self._highs, cfg.breakout_len)
        donchian_lo = self._lowest(self._lows, cfg.breakout_len)
        fs_hi = self._highest(self._highs, cfg.failsafe_len)
        fs_lo = self._lowest(self._lows, cfg.failsafe_len)
        exit_hi = self._highest(self._highs, cfg.trailing_exit_len)
        exit_lo = self._lowest(self._lows, cfg.trailing_exit_len)

        # 2. N = previous bar's ATR; then fold this bar's TR into the EMA.
        n = self._atr
        self._update_atr(self._true_range(high, low))
        self.current_bar += 1

        # 3. Risk-based unit size for this bar.
        units = self._turtle_units(n)

        actions: list[TradeAction] = []
        reasons: list[str] = []
        sent_order_this_bar = False
        entered_this_bar = False

        tradable = n is not None and n > 0 and units >= 1 and self.current_bar > cfg.atr_length

        if self.position == 0:
            # -- short-period breakout, gated by the last-profitable-trade filter
            filter_ok = (not cfg.last_profitable_trade_filter) or self.pre_breakout_failure
            if tradable and filter_ok and donchian_hi is not None and donchian_lo is not None:
                if high > donchian_hi:
                    price = min(high, donchian_hi + mp)
                    price = open_ if price < open_ else price  # big gap up -> open
                    self._open(1, units, price)
                    actions.append(TradeAction(BUY, units, "turtle_long_breakout", fill_price=price))
                    reasons.append("long_breakout")
                    entered_this_bar = sent_order_this_bar = True
                elif low < donchian_lo:
                    price = max(low, donchian_lo - mp)
                    price = open_ if price > open_ else price  # big gap down -> open
                    self._open(-1, units, price)
                    actions.append(TradeAction(SELL, units, "turtle_short_breakout", fill_price=price))
                    reasons.append("short_breakout")
                    entered_this_bar = sent_order_this_bar = True

            # -- long-period failsafe breakout (NOT gated by the filter)
            if tradable and not entered_this_bar and fs_hi is not None and fs_lo is not None:
                if high > fs_hi:
                    price = min(high, fs_hi + mp)
                    price = open_ if price < open_ else price
                    self._open(1, units, price)
                    actions.append(TradeAction(BUY, units, "turtle_long_failsafe", fill_price=price))
                    reasons.append("long_failsafe")
                    entered_this_bar = sent_order_this_bar = True
                elif low < fs_lo:
                    price = max(low, fs_lo - mp)
                    price = open_ if price > open_ else price
                    self._open(-1, units, price)
                    actions.append(TradeAction(SELL, units, "turtle_short_failsafe", fill_price=price))
                    reasons.append("short_failsafe")
                    entered_this_bar = sent_order_this_bar = True

        elif self.position == 1:  # long
            if exit_lo is not None and low < exit_lo:
                price = max(low, exit_lo - mp)
                price = open_ if price > open_ else price
                actions.append(TradeAction(SELL, self.units, "turtle_long_trailing_exit",
                                           close_all=True, fill_price=price))
                self._flat()
                reasons.append("long_trailing_exit")
            else:
                added, sent_order_this_bar = self._pyramid_long(open_, high, n, units, actions, reasons)
                if not sent_order_this_bar and self.pre_entry_price is not None and n is not None:
                    stop = self.pre_entry_price - 2.0 * n
                    if low <= stop:
                        price = open_ if stop > open_ else stop  # gap down through stop
                        actions.append(TradeAction(SELL, self.units, "turtle_long_stop",
                                                   close_all=True, fill_price=price))
                        self._flat()
                        self.pre_breakout_failure = True
                        reasons.append("long_stop")

        elif self.position == -1:  # short
            if exit_hi is not None and high > exit_hi:
                price = min(high, exit_hi + mp)
                price = open_ if price < open_ else price
                actions.append(TradeAction(BUY, self.units, "turtle_short_trailing_exit",
                                           close_all=True, fill_price=price))
                self._flat()
                reasons.append("short_trailing_exit")
            else:
                added, sent_order_this_bar = self._pyramid_short(open_, low, n, units, actions, reasons)
                if not sent_order_this_bar and self.pre_entry_price is not None and n is not None:
                    stop = self.pre_entry_price + 2.0 * n
                    if high >= stop:
                        price = open_ if stop < open_ else stop  # gap up through stop
                        actions.append(TradeAction(BUY, self.units, "turtle_short_stop",
                                                   close_all=True, fill_price=price))
                        self._flat()
                        self.pre_breakout_failure = True
                        reasons.append("short_stop")

        # 4. Roll the prior-bar buffers AFTER the decision (no look-ahead).
        self._highs.append(high)
        self._lows.append(low)
        self._prev_close = close

        label = self._label(actions)
        reason = ",".join(reasons) if reasons else ("warmup" if not tradable else "hold")
        return label, actions, reason

    # -- pyramiding ----------------------------------------------------------

    def _pyramid_long(self, open_, high, n, units, actions, reasons):
        """Add long units per +0.5N move (open-gap add + while-loop). Returns (added, sent)."""
        if self.pre_entry_price is None or n is None or units < 1:
            return False, False
        sent = False
        step = 0.5 * n
        if open_ >= self.pre_entry_price + step and self.current_entries < self.cfg.n_entries:
            price = open_
            self.pre_entry_price = price
            self._add(1, units, price)
            actions.append(TradeAction(BUY, units, "turtle_long_add", fill_price=price))
            reasons.append("long_add_gap")
            sent = True
        while high >= self.pre_entry_price + step and self.current_entries < self.cfg.n_entries:
            price = self.pre_entry_price + step
            self.pre_entry_price = price
            self._add(1, units, price)
            actions.append(TradeAction(BUY, units, "turtle_long_add", fill_price=price))
            reasons.append("long_add")
            sent = True
        return sent, sent

    def _pyramid_short(self, open_, low, n, units, actions, reasons):
        """Add short units per -0.5N move (open-gap add + while-loop). Returns (added, sent)."""
        if self.pre_entry_price is None or n is None or units < 1:
            return False, False
        sent = False
        step = 0.5 * n
        if open_ <= self.pre_entry_price - step and self.current_entries < self.cfg.n_entries:
            price = open_
            self.pre_entry_price = price
            self._add(-1, units, price)
            actions.append(TradeAction(SELL, units, "turtle_short_add", fill_price=price))
            reasons.append("short_add_gap")
            sent = True
        while low <= self.pre_entry_price - step and self.current_entries < self.cfg.n_entries:
            price = self.pre_entry_price - step
            self.pre_entry_price = price
            self._add(-1, units, price)
            actions.append(TradeAction(SELL, units, "turtle_short_add", fill_price=price))
            reasons.append("short_add")
            sent = True
        return sent, sent

    # -- position bookkeeping ------------------------------------------------

    def _open(self, direction: int, units: float, price: float) -> None:
        self.position = direction
        self.units = units
        self.current_entries = 1
        self.pre_entry_price = price
        self.pre_breakout_failure = False

    def _add(self, direction: int, units: float, price: float) -> None:
        self.units += units
        self.current_entries += 1
        # pre_entry_price already advanced by the caller (the +/-0.5N step price)

    def _flat(self) -> None:
        self.position = 0
        self.units = 0.0
        self.current_entries = 0
        self.pre_entry_price = None

    @staticmethod
    def _label(actions: list[TradeAction]) -> str:
        if not actions:
            return HOLD
        if any(a.close_all for a in actions):
            return EXIT
        return BUY if actions[0].side == BUY else SELL
