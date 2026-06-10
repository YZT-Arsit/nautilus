from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nautilus_ext.strategies.signal_types import BarInput


@dataclass(frozen=True)
class FeatureSnapshot:
    values: dict
    debug: dict | None = None


class BarFeatureEngine(Protocol):
    def reset(self) -> None:
        ...

    def update(self, bar: BarInput) -> FeatureSnapshot:
        ...
