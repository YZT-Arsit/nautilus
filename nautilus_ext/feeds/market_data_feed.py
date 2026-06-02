from __future__ import annotations

from typing import Protocol


class MarketDataFeed(Protocol):
    def initialize(self):
        ...

    def warmup(self, input_schema=None):
        ...

    def poll_once(self):
        ...

    def close(self) -> None:
        ...
