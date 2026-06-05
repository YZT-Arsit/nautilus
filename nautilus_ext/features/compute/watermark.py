"""
WatermarkTracker — event-time progress tracking for streaming feature windows.

In streaming systems, out-of-order events are normal. The watermark is the
engine's estimate of how far event time has progressed, accounting for the
maximum expected lateness of late events.

    watermark_ns = max_event_time_seen_ns - allowed_lateness_ns

A time window [start, end] is "finalized" when watermark_ns >= end, meaning
no future event can arrive that would change the window's content.

Late event definition
---------------------
An event is "late" when its event_time_ns < watermark_ns. The engine checks
this before calling feature.update() and dispatches to the appropriate late
event policy configured in TriggerPolicy.

Watermark advancement
---------------------
The watermark advances monotonically: it only moves forward when a new
event carries an event_time_ns higher than any previously seen. The
allowed_lateness_ns provides a safety margin so that slightly out-of-order
events (common in production) are not incorrectly discarded.

Backtest semantics
------------------
During warmup / backtest replay, events arrive in timestamp order so no
late events occur. The late_event_policy="recompute_for_backtest_only"
is a no-op in backtest mode (all events are processed normally), and acts
as "drop" in live mode.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class WatermarkTracker:
    """Tracks event-time progress and detects late events.

    Parameters
    ----------
    allowed_lateness_ns : int
        Safety margin in nanoseconds. Events arriving within this window
        behind the leading edge are still considered on-time.
        Default 0 (no tolerance).
    """

    def __init__(self, allowed_lateness_ns: int = 0) -> None:
        self._allowed_lateness_ns: int = allowed_lateness_ns
        self._max_event_time_ns: int = 0
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def update(self, event_time_ns: int) -> None:
        """Advance the watermark if event_time_ns is a new maximum."""
        if not self._initialized or event_time_ns > self._max_event_time_ns:
            self._max_event_time_ns = event_time_ns
            self._initialized = True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @property
    def watermark_ns(self) -> int:
        """Current watermark = max_event_time_seen - allowed_lateness_ns."""
        return max(0, self._max_event_time_ns - self._allowed_lateness_ns)

    @property
    def max_event_time_ns(self) -> int:
        return self._max_event_time_ns

    @property
    def is_initialized(self) -> bool:
        """False until at least one event has been processed."""
        return self._initialized

    def is_late(self, event_time_ns: int) -> bool:
        """Return True when event_time_ns is strictly before the watermark.

        An uninitialized tracker never reports lateness (no data yet).
        """
        return self._initialized and event_time_ns < self.watermark_ns

    def is_late_for(self, event_time_ns: int, allowed_lateness_ns: int) -> bool:
        """Lateness check with a per-call allowed_lateness override.

        Used when different features have different allowed_lateness_ns.
        """
        if not self._initialized:
            return False
        effective_watermark = max(0, self._max_event_time_ns - allowed_lateness_ns)
        return event_time_ns < effective_watermark

    def should_finalize_window(self, window_end_ns: int) -> bool:
        """Return True when the watermark has passed window_end_ns.

        A finalized window will not change: no in-order event can arrive
        that falls inside it anymore.
        """
        return self.watermark_ns >= window_end_ns

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._max_event_time_ns = 0
        self._initialized = False

    def state_dict(self) -> dict:
        return {
            "allowed_lateness_ns": self._allowed_lateness_ns,
            "max_event_time_ns": self._max_event_time_ns,
            "initialized": self._initialized,
        }

    def load_state_dict(self, state: dict) -> None:
        self._allowed_lateness_ns = state["allowed_lateness_ns"]
        self._max_event_time_ns = state["max_event_time_ns"]
        self._initialized = state["initialized"]
