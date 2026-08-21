"""Lookahead-safe composition of completed-bar feature snapshots.

This is an additive adapter over ``SpecFeatureEngine``.  It neither resamples
market data nor defines indicators: callers feed canonical, already completed
bars for each timeframe and strategies receive one namespaced snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from feature_engine.api import FeatureSnapshot, FeatureSpec, SpecFeatureEngine


@dataclass(frozen=True)
class CompletedFrameSnapshot:
    timeframe: str
    completed_at_ns: int
    snapshot: FeatureSnapshot


class CompletedBarAligner:
    """Expose only frame snapshots whose bar close is not later than a decision."""

    def __init__(self) -> None:
        self._latest: dict[str, CompletedFrameSnapshot] = {}

    def publish(self, timeframe: str, *, completed_at_ns: int, snapshot: FeatureSnapshot) -> None:
        previous = self._latest.get(timeframe)
        if previous is not None and completed_at_ns < previous.completed_at_ns:
            raise ValueError(f"{timeframe} completed-bar time moved backwards")
        self._latest[timeframe] = CompletedFrameSnapshot(timeframe, completed_at_ns, snapshot)

    def at(self, decision_time_ns: int, *, required: tuple[str, ...] = ()) -> FeatureSnapshot:
        values = {}
        instrument_id = None
        for timeframe, item in sorted(self._latest.items()):
            if item.completed_at_ns > decision_time_ns:
                continue
            instrument_id = instrument_id or item.snapshot.instrument_id
            for name, value in item.snapshot.values.items():
                values[f"{timeframe}.{name}"] = value
        missing = [frame for frame in required if not any(key.startswith(f"{frame}.") for key in values)]
        if missing:
            raise LookupError(f"no completed snapshot available for: {', '.join(missing)}")
        return FeatureSnapshot(ts_event=decision_time_ns, instrument_id=instrument_id, values=values)


class MultiTimeframeConfluence:
    """Separate persistent latest-completed state from one-shot triggers."""

    def __init__(self, timeframes: tuple[str, ...]) -> None:
        if not timeframes or len(set(timeframes)) != len(timeframes):
            raise ValueError("timeframes must be non-empty and unique")
        self._required = timeframes
        self._state: dict[str, bool] = {}
        self._trigger_time: dict[str, int] = {}
        self._completed_at: dict[str, int] = {}

    def publish(
        self, timeframe: str, *, completed_at_ns: int, state: bool,
        triggered: bool = False,
    ) -> None:
        if timeframe not in self._required:
            raise KeyError(f"unexpected timeframe {timeframe!r}")
        previous = self._completed_at.get(timeframe)
        if previous is not None and completed_at_ns < previous:
            raise ValueError(f"{timeframe} confluence state moved backwards")
        self._completed_at[timeframe] = completed_at_ns
        self._state[timeframe] = bool(state)
        if triggered:
            self._trigger_time[timeframe] = completed_at_ns

    def state_confluence(self) -> bool:
        return len(self._state) == len(self._required) and all(
            self._state[timeframe] for timeframe in self._required
        )

    def trigger_confluence(self, *, completed_at_ns: int) -> bool:
        return all(
            self._trigger_time.get(timeframe) == completed_at_ns
            for timeframe in self._required
        )


class MultiTimeframeFeatureStrategyRunner:
    """Normal feature engines plus a completed-bar alignment adapter."""

    def __init__(self, specs_by_timeframe: dict[str, list[FeatureSpec]], strategy: Any) -> None:
        if not specs_by_timeframe:
            raise ValueError("at least one timeframe is required")
        self._engines = {
            frame: SpecFeatureEngine(specs=specs, stamp_process_time=False)
            for frame, specs in specs_by_timeframe.items()
        }
        self._strategy = strategy
        self._aligner = CompletedBarAligner()

    def on_completed_bar(
        self, timeframe: str, event: Any, *, completed_at_ns: int,
    ) -> FeatureSnapshot:
        try:
            engine = self._engines[timeframe]
        except KeyError:
            raise KeyError(f"unknown timeframe {timeframe!r}") from None
        snapshot = engine.on_event(event)
        self._aligner.publish(timeframe, completed_at_ns=completed_at_ns, snapshot=snapshot)
        return snapshot

    def decide(self, decision_time_ns: int) -> tuple[FeatureSnapshot, Any]:
        snapshot = self._aligner.at(decision_time_ns, required=tuple(self._engines))
        return snapshot, self._strategy.on_snapshot(snapshot)
