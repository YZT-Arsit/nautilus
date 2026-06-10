"""Warmup / live split helper."""
from __future__ import annotations

from typing import Any


def split_warmup_live(events: list[Any], warmup_bars: int) -> tuple[list[Any], list[Any]]:
    """Split a flat event list into ``(warmup, live)`` at ``warmup_bars``."""
    n = max(0, int(warmup_bars))
    return events[:n], events[n:]
