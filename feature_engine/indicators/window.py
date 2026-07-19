"""Stateless window helpers over a caller-provided sequence.

The strategy keeps its own rolling ``deque`` and passes the current window slice
here; these functions carry no state. They reproduce the exact maths the strategy
ports used inline:

* ``rolling_std``  — standard deviation with an explicit divisor: ``ddof=1`` is the
  sample std (TB ``StandardDev`` DataType 2), ``ddof=0`` the population std
  (DataType 1). Returns ``0.0`` when the window is too small.
* ``highest`` / ``lowest`` — Donchian channel extremes over the window.
* ``round_half_up`` — TradeBlazer ``Round(x, 0)`` (round half away toward +inf via
  ``floor(x + 0.5)``, matching the ports' adaptive-lookback rounding).
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def rolling_std(window: Sequence[float], ddof: int) -> float:
    """Std-dev of ``window`` with divisor ``len - ddof``; ``0.0`` if too small."""
    n = len(window)
    if n - ddof <= 0:
        return 0.0
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / (n - ddof)
    return math.sqrt(var)


def highest(window: Sequence[float]) -> float:
    """Donchian upper — max of the window."""
    return max(window)


def lowest(window: Sequence[float]) -> float:
    """Donchian lower — min of the window."""
    return min(window)


def round_half_up(x: float) -> int:
    """TradeBlazer ``Round(x, 0)``: half rounds up (``floor(x + 0.5)``)."""
    return math.floor(x + 0.5)
