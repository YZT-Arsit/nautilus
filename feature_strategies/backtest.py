"""Minimal, dependency-free signal recorder for historical replay.

This is *not* a trading engine — there is no PnL, no fills, no positions yet.
It captures each ``(event, snapshot, signal)`` as a flat record so a backtest
run is traceable and can later feed performance metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SignalRecord:
    event_time_ns: int | None
    instrument_id: str | None
    signal: str
    close: float | None
    values: dict[str, float | None]


class SignalRecorder:
    """Collects :class:`SignalRecord` rows over a replay."""

    def __init__(self, spec_names: list[str]) -> None:
        self._spec_names = list(spec_names)
        self._records: list[SignalRecord] = []

    def record(self, event: Any, snapshot: Any, signal: str) -> None:
        self._records.append(
            SignalRecord(
                event_time_ns=getattr(event, "event_time_ns", None),
                instrument_id=getattr(event, "instrument_id", None),
                signal=signal,
                close=getattr(event, "close", None),
                values={name: snapshot.value(name) for name in self._spec_names},
            )
        )

    def records(self) -> list[SignalRecord]:
        return list(self._records)

    def signal_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._records:
            counts[record.signal] = counts.get(record.signal, 0) + 1
        return counts

    def to_rows(self) -> list[dict[str, object]]:
        """Flatten records into plain dicts (feature values inlined by name)."""
        rows: list[dict[str, object]] = []
        for r in self._records:
            row: dict[str, object] = {
                "event_time_ns": r.event_time_ns,
                "instrument_id": r.instrument_id,
                "signal": r.signal,
                "close": r.close,
            }
            row.update(r.values)
            rows.append(row)
        return rows
