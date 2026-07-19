"""Four-MA Crossover long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``FourSetofMACrossoverSys_L`` system:

* eight SMAs of close (defaults collapse to four distinct periods 3/5/10/20): a
  long-entry pair ``MALEFast``(5)/``MALESlow``(20), a long-exit pair
  ``MALXFast``(3)/``MALXSlow``(10), and mirror short pairs;
* entry (long): not long, ``CurrentBar >= min_bars``, ``MALEFast[1] >
  MALESlow[1]`` and ``MALXFast[1] > MALXSlow[1]`` (both pairs bullishly arranged),
  ``High >= High[1]`` and ``Vol > 0`` -> long at ``Max(Open, High[1])``;
* exit (sell), once ``BarsSinceEntry > 0`` and ``Vol > 0``: ``MALXFast[1] <
  MALXSlow[1]`` (the long-exit pair flips bearish) -> sell at ``Open``.

Fidelity note: the source's second exit branch (``MASEFast[1] < MASESlow[1] And
MASXFast[1] < MASXSlow[1] And Low <= Low[1]``) is preserved verbatim but is
**dead code** — ``MASXFast``/``MASXSlow`` share the 3/10 periods of
``MALXFast``/``MALXSlow``, so its ``MASXFast[1] < MASXSlow[1]`` clause can only be
true when the first branch already fired; it is kept for parity and never
triggers with the default periods.

Faithful TradeBlazer semantics preserved: every MA comparison reads the ``[1]``
(previous-bar) value snapshotted before the roll; the entry / exit read
``High[1]`` / ``Low[1]``; ``MarketPosition`` uses the bar-start position and the
exit is gated by ``BarsSinceEntry > 0``, so entry and sell never fire on one bar.
There **is** a ``Vol > 0`` gate. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.four_ma_crossover_long.config import FourMaCrossoverLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class FourMaCrossoverLongEngine:
    """Pure, position-aware Four-MA Crossover long engine."""

    def __init__(self, config: FourMaCrossoverLongConfig) -> None:
        self.cfg = config
        max_len = max(config.le_fast, config.le_slow, config.lx_fast, config.lx_slow,
                      config.se_fast, config.se_slow, config.sx_fast, config.sx_slow)
        self._closes: deque[float] = deque(maxlen=max_len)

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev: dict[str, float | None] = {}
        self._prev_low: float | None = None
        self._prev_high: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sma(list(self._closes)[-period:])

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._bar += 1
        cb = self._bar

        # 1. Moving averages (current bar inclusive).
        self._closes.append(close)
        cur = {
            "le_fast": self._sma(cfg.le_fast), "le_slow": self._sma(cfg.le_slow),
            "lx_fast": self._sma(cfg.lx_fast), "lx_slow": self._sma(cfg.lx_slow),
            "se_fast": self._sma(cfg.se_fast), "se_slow": self._sma(cfg.se_slow),
            "sx_fast": self._sma(cfg.sx_fast), "sx_slow": self._sma(cfg.sx_slow),
        }
        p = self._prev

        def ready(*keys: str) -> bool:
            return all(p.get(k) is not None for k in keys)

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open long): both MA pairs bullish + a higher high.
        if (
            not acted and mp_start != 1 and cb >= cfg.min_bars
            and ready("le_fast", "le_slow", "lx_fast", "lx_slow") and self._prev_high is not None
            and p["le_fast"] > p["le_slow"] and p["lx_fast"] > p["lx_slow"]
            and high >= self._prev_high and volume > 0
        ):
            entry_price = max(open_, self._prev_high)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = BUY, "enter_long", True

        # 3. EXIT (sell): long-exit pair flips bearish, else the (dead) bear branch.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0:
            if ready("lx_fast", "lx_slow") and p["lx_fast"] < p["lx_slow"]:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_ma_cross", True
            elif (
                ready("se_fast", "se_slow", "sx_fast", "sx_slow") and self._prev_low is not None
                and p["se_fast"] < p["se_slow"] and p["sx_fast"] < p["sx_slow"]
                and low <= self._prev_low
            ):
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_bear", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev = cur
        self._prev_low = low
        self._prev_high = high
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
