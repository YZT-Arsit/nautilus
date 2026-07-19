"""Four-MA Crossover short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``FourSetofMACrossoverSys_S`` system:

* eight SMAs of close (defaults collapse to four distinct periods 3/5/10/20): a
  short-entry pair ``MASEFast``(5)/``MASESlow``(20), a short-exit pair
  ``MASXFast``(3)/``MASXSlow``(10), and mirror long pairs;
* entry (short): not short, ``CurrentBar >= min_bars``, ``MASEFast[1] <
  MASESlow[1]`` and ``MASXFast[1] < MASXSlow[1]`` (both pairs bearishly arranged),
  ``Low <= Low[1]`` and ``Vol > 0`` -> short at ``Min(Open, Low[1])``;
* exit (cover), once ``BarsSinceEntry > 0`` and ``Vol > 0``: ``MASXFast[1] >
  MASXSlow[1]`` (the short-exit pair flips bullish) -> cover at ``Open``.

Fidelity note: the source's second exit branch (``MALEFast[1] > MALESlow[1] And
MALXFast[1] > MALXSlow[1] And High >= High[1]``) is preserved verbatim but is
**dead code** — ``MALXFast``/``MALXSlow`` share the 3/10 periods of
``MASXFast``/``MASXSlow``, so its ``MALXFast[1] > MALXSlow[1]`` clause can only be
true when the first branch already fired; it is kept for parity and never
triggers with the default periods.

Faithful TradeBlazer semantics preserved: every MA comparison reads the ``[1]``
(previous-bar) value snapshotted before the roll; the entry / exit read
``Low[1]`` / ``High[1]``; ``MarketPosition`` uses the bar-start position and the
exit is gated by ``BarsSinceEntry > 0``, so entry and cover never fire on one bar.
There **is** a ``Vol > 0`` gate. ``Average`` is a simple mean.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.four_ma_crossover_short.config import FourMaCrossoverShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class FourMaCrossoverShortEngine:
    """Pure, position-aware Four-MA Crossover short engine."""

    def __init__(self, config: FourMaCrossoverShortConfig) -> None:
        self.cfg = config
        max_len = max(config.se_fast, config.se_slow, config.sx_fast, config.sx_slow,
                      config.le_fast, config.le_slow, config.lx_fast, config.lx_slow)
        self._closes: deque[float] = deque(maxlen=max_len)

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
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
            "se_fast": self._sma(cfg.se_fast), "se_slow": self._sma(cfg.se_slow),
            "sx_fast": self._sma(cfg.sx_fast), "sx_slow": self._sma(cfg.sx_slow),
            "le_fast": self._sma(cfg.le_fast), "le_slow": self._sma(cfg.le_slow),
            "lx_fast": self._sma(cfg.lx_fast), "lx_slow": self._sma(cfg.lx_slow),
        }
        p = self._prev

        def ready(*keys: str) -> bool:
            return all(p.get(k) is not None for k in keys)

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): both MA pairs bearish + a lower low.
        if (
            not acted and mp_start != -1 and cb >= cfg.min_bars
            and ready("se_fast", "se_slow", "sx_fast", "sx_slow") and self._prev_low is not None
            and p["se_fast"] < p["se_slow"] and p["sx_fast"] < p["sx_slow"]
            and low <= self._prev_low and volume > 0
        ):
            entry_price = min(open_, self._prev_low)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): short-exit pair flips bullish, else the (dead) bull branch.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            if ready("sx_fast", "sx_slow") and p["sx_fast"] > p["sx_slow"]:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_ma_cross", True
            elif (
                ready("le_fast", "le_slow", "lx_fast", "lx_slow") and self._prev_high is not None
                and p["le_fast"] > p["le_slow"] and p["lx_fast"] > p["lx_slow"]
                and high >= self._prev_high
            ):
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_bull", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev = cur
        self._prev_low = low
        self._prev_high = high
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
