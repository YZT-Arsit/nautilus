"""Stable execution helper: wire feature specs + a strategy into one driver.

:class:`FeatureStrategyRunner` owns the two pieces of boilerplate every demo and
strategy harness repeats — constructing a :class:`SpecFeatureEngine` from specs,
and running the warmup / per-event loop that feeds snapshots to a strategy.

It carries no feature maths and no trading logic. The engine is reached only
through its public methods; the strategy only needs ``on_snapshot``.

Example
-------
    from feature_engine.runner import FeatureStrategyRunner
    from strategies.ma_crossover import (
        MovingAverageCrossoverConfig, MovingAverageCrossoverStrategy, build_specs,
    )

    config = MovingAverageCrossoverConfig()
    runner = FeatureStrategyRunner(build_specs(config), MovingAverageCrossoverStrategy(config))
    runner.warmup(warmup_bars)
    for event, snapshot, signal in runner.run(live_bars):
        ...
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

from feature_engine.api import FeatureSnapshot, FeatureSpec, SpecFeatureEngine


class FeatureStrategyRunner:
    """Drive a strategy from a spec-built feature engine over a stream of events.

    Parameters
    ----------
    specs
        Feature specifications; used to build the internal ``SpecFeatureEngine``.
    strategy
        Any object exposing ``on_snapshot(snapshot) -> signal``.
    engine_kwargs
        Extra keyword args forwarded to ``SpecFeatureEngine``. ``stamp_process_time``
        defaults to ``False`` here (deterministic demos/tests); override if you
        want wall-clock process stamps.
    """

    def __init__(
        self,
        specs: list[FeatureSpec],
        strategy: Any,
        *,
        engine_kwargs: dict[str, Any] | None = None,
    ) -> None:
        kwargs = dict(engine_kwargs or {})
        kwargs.setdefault("stamp_process_time", False)
        self._engine = SpecFeatureEngine(specs=specs, **kwargs)
        self._strategy = strategy

    def warmup(self, events: Iterable[Any]) -> None:
        """Pre-heat features and an optional strategy warmup hook, without orders."""
        hook = getattr(self._strategy, "on_warmup_snapshot", None)
        for event in events:
            snapshot = self._engine.on_event(event)
            if hook is not None:
                hook(snapshot)

    def on_event(self, event: Any) -> tuple[FeatureSnapshot, Any]:
        """Process one live event, returning ``(snapshot, signal)``."""
        snapshot = self._engine.on_event(event)
        signal = self._strategy.on_snapshot(snapshot)
        return snapshot, signal

    def run(self, events: Iterable[Any]) -> Iterator[tuple[Any, FeatureSnapshot, Any]]:
        """Yield ``(event, snapshot, signal)`` for each live event, in order."""
        for event in events:
            snapshot, signal = self.on_event(event)
            yield event, snapshot, signal

    def value(self, name: str, default: Any = None) -> Any:
        """Current value of a feature, or ``default`` if absent / not ready."""
        return self._engine.value(name, default)

    def is_ready(self, name: str | None = None) -> bool:
        """Whether a named feature (or all features) are ready."""
        return self._engine.is_ready(name)

    def health_summary(self, stale_threshold_ns: int | None = None) -> dict:
        """Feature health diagnostic report from the underlying engine."""
        return self._engine.health_summary(stale_threshold_ns)
