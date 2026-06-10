"""
Clock abstraction for deterministic process_time_ns stamping.

In production (live trading): use SystemClock, which wraps time.time_ns().
In tests and deterministic backtest replay: use ManualClock to control the
clock explicitly so that process_time_ns is reproducible.

The engine accepts any object satisfying the Clock protocol (structural typing,
no inheritance required). This makes mocking trivial without patching.

    from nautilus_ext.features.compute.clock import ManualClock
    clock = ManualClock(initial_ns=1_000_000_000)
    engine = SpecFeatureEngine(specs=specs, clock=clock, stamp_process_time=True)
    snap = engine.on_event(bar)
    assert snap.process_time_ns == 1_000_000_000   # deterministic
"""
from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Structural protocol: any object with now_ns() satisfies this."""

    def now_ns(self) -> int:
        """Return current time as a nanosecond POSIX timestamp."""
        ...


class SystemClock:
    """Live clock backed by time.time_ns().

    Default clock for SpecFeatureEngine when stamp_process_time=True.
    """

    def now_ns(self) -> int:
        return time.time_ns()


class ManualClock:
    """Deterministic clock for tests and backtest replay.

    Parameters
    ----------
    initial_ns : int
        Starting timestamp in nanoseconds (default 0).

    Examples
    --------
    ::

        clock = ManualClock(initial_ns=1_000_000_000)
        engine = SpecFeatureEngine(specs=specs, clock=clock)
        clock.advance(500_000)          # advance 0.5 ms
        snap = engine.on_event(bar)
        assert snap.processing_latency_ns() == 500_000
    """

    def __init__(self, initial_ns: int = 0) -> None:
        self._ns: int = initial_ns

    def now_ns(self) -> int:
        return self._ns

    def set(self, ns: int) -> None:
        """Teleport the clock to an absolute nanosecond timestamp."""
        self._ns = ns

    def advance(self, delta_ns: int) -> None:
        """Advance the clock by delta_ns nanoseconds."""
        self._ns += delta_ns
