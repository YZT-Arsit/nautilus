"""MA5 / MA20 moving-average crossover strategy — boss-facing definition.

This is the file a strategy author edits. It declares *what* the strategy needs
(two moving averages) and *how* it turns them into BUY / SELL / HOLD signals.

It says nothing about *how* features are computed. It imports only the stable
public API (:mod:`nautilus_ext.features.api`) and touches feature values purely
through :class:`FeatureSnapshot`. The low-level operator library lives in
``nautilus_ext/features/compute/`` and is not imported here.

Signal rules
------------
    BUY   : fast MA crosses above slow MA  (prev_fast <= prev_slow AND fast > slow)
    SELL  : fast MA crosses below slow MA  (prev_fast >= prev_slow AND fast < slow)
    HOLD  : otherwise, or whenever either MA is not yet ready
"""
from __future__ import annotations

from dataclasses import dataclass

from nautilus_ext.features.api import FeatureSnapshot, FeatureSpec

BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"


@dataclass(frozen=True)
class MovingAverageCrossoverConfig:
    """Parameters for a two-line moving-average crossover.

    ``fast_name`` / ``slow_name`` are the feature names the strategy reads from
    each snapshot; they must match the names produced by :func:`build_specs`.
    """

    fast_window: int = 5
    slow_window: int = 20
    fast_name: str = "ma5_close"
    slow_name: str = "ma20_close"
    input_type: str = "bar"
    input_field: str = "close"


def build_specs(config: MovingAverageCrossoverConfig) -> list[FeatureSpec]:
    """Return the two ``rolling_mean`` specs the strategy depends on.

    The compute layer turns each spec into a rolling-mean feature; the strategy
    only ever refers to them by ``config.fast_name`` / ``config.slow_name``.
    """
    return [
        FeatureSpec(
            config.fast_name,
            input_type=config.input_type,
            input_field=config.input_field,
            window=config.fast_window,
            params={"type": "rolling_mean"},
        ),
        FeatureSpec(
            config.slow_name,
            input_type=config.input_type,
            input_field=config.input_field,
            window=config.slow_window,
            params={"type": "rolling_mean"},
        ),
    ]


# Backward-compatible alias for the previous name.
build_ma_crossover_specs = build_specs


class MovingAverageCrossoverStrategy:
    """Emit BUY / SELL / HOLD from successive feature snapshots.

    The strategy keeps the previous fast/slow MA values internally so it can
    detect a *crossover* (a change of sign in ``fast - slow``) between two
    consecutive ready snapshots. A crossover therefore needs two ready snapshots
    in a row; the first ready snapshot always yields HOLD.
    """

    def __init__(self, config: MovingAverageCrossoverConfig) -> None:
        self._config = config
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        """Return the signal for this snapshot and advance internal state."""
        fast = snapshot.value(self._config.fast_name)
        slow = snapshot.value(self._config.slow_name)
        signal = self._classify(fast, slow)
        self._prev_fast = fast
        self._prev_slow = slow
        return signal

    def _classify(self, fast: float | None, slow: float | None) -> str:
        prev_fast, prev_slow = self._prev_fast, self._prev_slow
        if None in (fast, slow, prev_fast, prev_slow):
            return HOLD
        if prev_fast <= prev_slow and fast > slow:
            return BUY
        if prev_fast >= prev_slow and fast < slow:
            return SELL
        return HOLD
