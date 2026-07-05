"""Dual-MA (stop-and-reverse) — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Unlike the single-side
ports it is **always in the market** and flips between long and short, so it uses
the rich-plan path: ``update`` returns ``(label, actions, reason)`` where
``actions`` is a list of sized :class:`TradeAction` s (as ``turtle_trader`` does).

Ported from the TradeBlazer ``DualMA`` system:

* ``AvgValue1 = AverageFC(Close, FastLength)``, ``AvgValue2 = AverageFC(Close,
  SlowLength)`` (simple means);
* ``If MarketPosition <> 1 And AvgValue1[1] > AvgValue2[1]`` -> ``Buy(Open)`` (go
  long / reverse from short);
* ``If MarketPosition <> -1 And AvgValue1[1] < AvgValue2[1]`` -> ``SellShort(Open)``
  (go short / reverse from long).

Faithful TradeBlazer semantics preserved: both MA comparisons read the
**previous-bar** values ``AvgValue1[1]``/``AvgValue2[1]`` snapshotted before the
roll; the ``MarketPosition <> 1`` / ``<> -1`` guards make the system fire only on a
regime change (once per flip, no pyramiding), using the bar-start position; orders
fill at this bar's ``Open`` (carried on the ``TradeAction`` as ``fill_price``).
A reversal submits ``|position| + 1`` units (one to flatten the existing side, one
to open the new) so the netting fill model lands on exactly one unit the other way.
Strict inequalities: an exact ``AvgValue1[1] == AvgValue2[1]`` holds the position.
``AverageFC`` is a simple mean. There is **no** ``Vol > 0`` gate (matches source).
"""
from __future__ import annotations

from collections import deque

from strategy_framework.execution.intents import TradeAction

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class DualMaEngine:
    """Pure, position-aware Dual-MA stop-and-reverse engine."""

    def __init__(self, config) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=max(config.fast_length, config.slow_length))

        # position state (+1 long, -1 short, 0 flat before the first signal)
        self.position = 0

        # previous-bar MA snapshots (the ``[1]`` values the decisions read)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def _sma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        return sum(list(self._closes)[-period:]) / period

    def update(self, open_: float, close: float):
        cfg = self.cfg
        self._closes.append(close)
        fast = self._sma(cfg.fast_length)
        slow = self._sma(cfg.slow_length)

        pf, ps = self._prev_fast, self._prev_slow
        mp_start = self.position
        actions: list[TradeAction] = []
        reason = "hold"

        if pf is not None and ps is not None:
            if mp_start != 1 and pf > ps:
                qty = (abs(mp_start) + 1) * cfg.contract_unit
                reason = "reverse_to_long" if mp_start == -1 else "enter_long"
                actions.append(TradeAction(BUY, qty, reason, fill_price=open_))
                self.position = 1
            elif mp_start != -1 and pf < ps:
                qty = (abs(mp_start) + 1) * cfg.contract_unit
                reason = "reverse_to_short" if mp_start == 1 else "enter_short"
                actions.append(TradeAction(SELL, qty, reason, fill_price=open_))
                self.position = -1

        # Roll the prev-bar MA snapshots.
        self._prev_fast = fast
        self._prev_slow = slow

        label = self._label(actions)
        return label, actions, reason

    @staticmethod
    def _label(actions: list[TradeAction]) -> str:
        if not actions:
            return HOLD
        return BUY if actions[0].side == BUY else SELL
