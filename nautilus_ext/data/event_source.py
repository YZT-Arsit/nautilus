from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class EventSource(Protocol):
    def iter_events(self) -> Iterable[object]:
        ...
