"""Superman System short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``SupermanSystem_S`` system:

* **MarketStrength** over ``length`` bars: ``SumChange = Σ(Close-Close[1])``;
  if ``SumChange >= 0`` -> ``SumChange / UpCloses * 100`` (0..100), else
  ``SumChange / |DnCloses| * 100`` (-100..0), where ``UpCloses`` / ``DnCloses``
  sum the positive / non-positive close-to-close changes in the window.
* **Momentum** ``Momentum1 = Close - Close[4]``, ``Momentum2 = Close[4] - Close[8]``.
* **Channels** ``HH1/LL = Highest(High,length)/Lowest(Low,length)`` (entry),
  ``HH2 = Highest(High,stop_len)`` (stop, sampled at the entry bar).
* entry (short): flat, ``MarketStrength[1] <= -entry_strength`` (strongly bearish),
  ``Momentum1[1] <= 0`` AND ``Momentum2[1] > 0`` (momentum flipped up->down),
  ``Low <= LL[1]`` (downside breakout), ``Vol > 0`` -> short at ``Min(Open, LL[1])``;
  record ``StopLoss = HH2`` (entry-bar channel high) and ``ProfitTarget =
  EntryPrice - (StopLoss - EntryPrice) * profit_factor``.
* exit (cover), once ``BarsSinceEntry > 0``: profit target ``Low <= ProfitTarget``
  -> ``Min(Open, ProfitTarget)``; else stop ``High >= StopLoss`` -> ``Max(Open,
  StopLoss)``; else reverse signal ``MarketStrength[1] >= entry_strength AND
  Momentum1[1] > 0 AND Momentum2[1] <= 0 AND High >= HH1[1]`` -> ``Max(Open, HH1[1])``.

Faithful TradeBlazer semantics preserved: the strength / momentum / channel inputs
to the entry & reverse-exit read the **previous** bar (``[1]``); ``StopLoss`` /
``ProfitTarget`` are fixed at the entry bar and held; ``MarketPosition == 0`` /
``== -1`` uses the bar-start position and the exit is gated by ``BarsSinceEntry >
0`` (an entry bar never exits). Exit priority: profit target -> stop -> reverse.

Fidelity note: guards ``UpCloses == 0`` / ``DnCloses == 0`` (a fully-flat window)
to ``MarketStrength = 0`` to avoid the div-by-zero the raw formula would hit.
"""
from __future__ import annotations

from collections import deque

from strategies.superman_short.config import SupermanShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class SupermanShortEngine:
    """Pure, position-aware Superman System short engine."""

    def __init__(self, config: SupermanShortConfig) -> None:
        self.cfg = config
        self._changes: deque[float] = deque(maxlen=config.length)   # close-to-close changes
        self._closes9: deque[float] = deque(maxlen=9)               # momentum: Close[0..8]
        self._highs_len: deque[float] = deque(maxlen=config.length)  # HH1
        self._lows_len: deque[float] = deque(maxlen=config.length)   # LL
        self._highs_stop: deque[float] = deque(maxlen=config.stop_len)  # HH2

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.stop_loss: float | None = None       # fixed at entry (HH2)
        self.profit_target: float | None = None   # fixed at entry

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_strength: float | None = None
        self._prev_mom1: float | None = None
        self._prev_mom2: float | None = None
        self._prev_hh1: float | None = None
        self._prev_ll: float | None = None

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
        self._highs_stop.append(high)
        hh1 = max(self._highs_len) if len(self._highs_len) == cfg.length else None
        ll = min(self._lows_len) if len(self._lows_len) == cfg.length else None
        hh2 = max(self._highs_stop) if len(self._highs_stop) == cfg.stop_len else None

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): bearish strength + up->down momentum + downside breakout.
        if (
            not acted and mp_start == 0
            and self._prev_strength is not None and self._prev_strength <= -cfg.entry_strength
            and self._prev_mom1 is not None and self._prev_mom1 <= 0
            and self._prev_mom2 is not None and self._prev_mom2 > 0
            and self._prev_ll is not None and low <= self._prev_ll
            and hh2 is not None
            and volume > 0
        ):
            entry_price = min(open_, self._prev_ll)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.stop_loss = hh2
            self.profit_target = entry_price - (hh2 - entry_price) * cfg.profit_factor
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): profit target -> stop -> reverse signal.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            if self.profit_target is not None and low <= self.profit_target:
                self._cover()
                signal, reason, acted = BUY, "exit_profit_target", True
            elif self.stop_loss is not None and high >= self.stop_loss:
                self._cover()
                signal, reason, acted = BUY, "exit_stop_loss", True
            elif (
                self._prev_strength is not None and self._prev_strength >= cfg.entry_strength
                and self._prev_mom1 is not None and self._prev_mom1 > 0
                and self._prev_mom2 is not None and self._prev_mom2 <= 0
                and self._prev_hh1 is not None and high >= self._prev_hh1
            ):
                self._cover()
                signal, reason, acted = BUY, "exit_reverse", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_close = close
        self._prev_strength = strength
        self._prev_mom1 = mom1
        self._prev_mom2 = mom2
        self._prev_hh1 = hh1
        self._prev_ll = ll
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
        self.stop_loss = None
        self.profit_target = None
