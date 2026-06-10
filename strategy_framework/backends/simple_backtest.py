"""Minimal in-process backtest backend.

Records the signal stream (no fills, no positions, no PnL) by delegating to the
existing :class:`strategy_framework.backtest.SignalRecorder`, then prints a count
summary on ``close()``. This is the default, dependency-free backend.
"""
from __future__ import annotations

from typing import Any

from strategy_framework.backtest import SignalRecord, SignalRecorder


class SimpleBacktestBackend:
    """Wraps :class:`SignalRecorder` behind the :class:`ExecutionBackend` API."""

    def __init__(self, spec_names: list[str]) -> None:
        self._recorder = SignalRecorder(list(spec_names))

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        self._recorder.record(event, snapshot, signal)

    def records(self) -> list[SignalRecord]:
        return self._recorder.records()

    def signal_counts(self) -> dict[str, int]:
        return self._recorder.signal_counts()

    def close(self) -> None:
        counts = self._recorder.signal_counts()
        summary = " ".join(f"{name}={count}" for name, count in sorted(counts.items()))
        print(f"[simple_backtest] signal counts: {summary}".rstrip())
