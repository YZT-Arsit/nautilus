"""The minimal event-source interface.

Every data source (synthetic, CSV, live) exposes two methods:

* ``warmup()`` — a finite iterable of historical events to pre-heat features.
* ``stream()`` — the (possibly unbounded) iterable of live events.

This is just an interface; there is no base implementation to inherit.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable


@runtime_checkable
class EventSource(Protocol):
    def warmup(self) -> Iterable[Any]: ...

    def stream(self) -> Iterable[Any]: ...
