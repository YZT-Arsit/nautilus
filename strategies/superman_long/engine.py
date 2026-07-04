"""Superman System long — pure decision engine (position-aware, offline-testable).

Long-side mirror of ``strategies/superman_short/engine.py``. Holds **only** the
signal-decision maths (plain-Python; no ``feature_engine`` / ``strategy_framework``
/ ``nautilus_trader`` / ``pandas``). Emits ``BUY``/``SELL``/``HOLD`` with the
signal->order meaning left to ``SignalToOrderPolicy`` (``sell_means: flat`` — BUY
opens the long, SELL flattens it). Single unit, no pyramiding.

Ported from the TradeBlazer ``SupermanSystem_L`` system:

* **MarketStrength** over ``length`` bars: ``SumChange = Σ(Close-Close[1])``;
  if ``SumChange >= 0`` -> ``SumChange / UpCloses * 100`` (0..100), else
  ``SumChange / |DnCloses| * 100`` (-100..0), where ``UpCloses`` / ``DnCloses``
  sum the positive / non-positive close-to-close changes in the window.
* **Momentum** ``Momentum1 = Close - Close[4]``, ``Momentum2 = Close[4] - Close[8]``.
* **Channels** ``HH/LL1 = Highest(High,length)/Lowest(Low,length)`` (entry),
  ``LL2 = Lowest(Low,stop_len)`` (stop, sampled at the entry bar).
* entry (long): flat, ``MarketStrength[1] >= entry_strength`` (strongly bullish),
  ``Momentum1[1] >= 0`` AND ``Momentum2[1] < 0`` (momentum flipped down->up),
  ``High >= HH[1]`` (upside breakout), ``Vol > 0`` -> long at ``Max(Open, HH[1])``;
  record ``StopLoss = LL2`` (entry-bar channel low) and ``ProfitTarget =
  EntryPrice + (EntryPrice - StopLoss) * profit_factor``.
* exit (sell), once ``BarsSinceEntry > 0``: profit target ``High >= ProfitTarget``
  -> ``Max(Open, ProfitTarget)``; else stop ``Low <= StopLoss`` -> ``Min(Open,
  StopLoss)``; else reverse signal ``MarketStrength[1] <= -entry_strength AND
  Momentum1[1] < 0 AND Momentum2[1] >= 0 AND Low <= LL1[1]`` -> ``Min(Open, LL1[1])``.

Faithful TradeBlazer semantics preserved (identical to the short engine, mirrored
to the long side): the strength / momentum / channel inputs to the entry &
reverse-exit read the **previous** bar (``[1]``); ``StopLoss`` / ``ProfitTarget``
are fixed at the entry bar and held; ``MarketPosition == 0`` / ``== 1`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0``. Exit priority:
profit target -> stop -> reverse. Guards ``UpCloses == 0`` / ``DnCloses == 0`` to
``MarketStrength = 0`` to avoid div-by-zero.
"""
from __future__ import annotations

from collections import deque

from strategies.superman_long.config import SupermanLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class SupermanLongEngine:
    """Pure, position-aware Superman System long engine."""

    def __init__(self, config: SupermanLongConfig) -> None:
        self.cfg = config
        self._changes: deque[float] = deque(maxlen=config.length)   # close-to-close changes
        self._closes9: deque[float] = deque(maxlen=9)               # momentum: Close[0..8]
        self._highs_len: deque[float] = deque(maxlen=config.length)  # HH
        self._lows_len: deque[float] = deque(maxlen=config.length)   # LL1
        self._lows_stop: deque[float] = deque(maxlen=config.stop_len)  # LL2

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.stop_loss: float | None = None       # fixed at entry (LL2)
        self.profit_target: float | None = None   # fixed at entry

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_strength: float | None = None
        self._prev_mom1: float | None = None
        self._prev_mom2: float | None = None
        self._prev_hh: float | None = None
        self._prev_ll1: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. current-bar indicators (decisions still read the prev-bar snapshots).
        if self._prev_close is not None:
            self._changes.append(close - self._prev_close)
        if len(self._changes) == cfg.length:
            up = sum(x for x in self._changes if x > 0)
            dn = sum(x for x in self._changes if x <= 0)
            sumc = sum(self._changes)
            if sumc >= 0:
                strength = sumc / up * 100.0 if up != 0 else 0.0
            else:
                strength = sumc / abs(dn) * 100.0 if dn != 0 else 0.0
        else:
            strength = None

        self._closes9.append(close)
        if len(self._closes9) == 9:
            mom1 = close - self._closes9[-5]            # Close - Close[4]
            mom2 = self._closes9[-5] - self._closes9[-9]  # Close[4] - Close[8]
        else:
            mom1 = mom2 = None

        self._highs_len.append(high)
        self._lows_len.append(low)
        self._lows_stop.append(low)
        hh = max(self._highs_len) if len(self._highs_len) == cfg.length else None
        ll1 = min(self._lows_len) if len(self._lows_len) == cfg.length else None
        ll2 = min(self._lows_stop) if len(self._lows_stop) == cfg.stop_len else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open long): bullish strength + down->up momentum + upside breakout.
        if (
            not acted and mp_start == 0
            and self._prev_strength is not None and self._prev_strength >= cfg.entry_strength
            and self._prev_mom1 is not None and self._prev_mom1 >= 0
            and self._prev_mom2 is not None and self._prev_mom2 < 0
            and self._prev_hh is not None and high >= self._prev_hh
            and ll2 is not None
            and volume > 0
        ):
            entry_price = max(open_, self._prev_hh)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.stop_loss = ll2
            self.profit_target = entry_price + (entry_price - ll2) * cfg.profit_factor
            signal, reason, acted = BUY, "enter_long", True

        # 3. EXIT (sell): profit target -> stop -> reverse signal.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0:
            if self.profit_target is not None and high >= self.profit_target:
                self._flat()
                signal, reason, acted = SELL, "exit_profit_target", True
            elif self.stop_loss is not None and low <= self.stop_loss:
                self._flat()
                signal, reason, acted = SELL, "exit_stop_loss", True
            elif (
                self._prev_strength is not None and self._prev_strength <= -cfg.entry_strength
                and self._prev_mom1 is not None and self._prev_mom1 < 0
                and self._prev_mom2 is not None and self._prev_mom2 >= 0
                and self._prev_ll1 is not None and low <= self._prev_ll1
            ):
                self._flat()
                signal, reason, acted = SELL, "exit_reverse", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_close = close
        self._prev_strength = strength
        self._prev_mom1 = mom1
        self._prev_mom2 = mom2
        self._prev_hh = hh
        self._prev_ll1 = ll1
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
        self.stop_loss = None
        self.profit_target = None
