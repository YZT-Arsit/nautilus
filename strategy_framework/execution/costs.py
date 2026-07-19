"""Execution-price cost models shared by fill backends."""
from __future__ import annotations

from dataclasses import replace

from strategy_framework.execution.reports import FillRecord


def apply_adverse_slippage(fill: FillRecord, slippage_bps: float) -> FillRecord:
    """Return a fill with deterministic side-adverse basis-point slippage."""
    bps = float(slippage_bps)
    if bps < 0:
        raise ValueError("slippage_bps must be non-negative")
    if bps == 0:
        return fill
    direction = 1.0 if fill.side == "BUY" else -1.0
    slipped = float(fill.price) * (1.0 + direction * bps / 10_000.0)
    metadata = {**(fill.metadata or {}), "raw_fill_price": float(fill.price), "slippage_bps": bps}
    return replace(fill, price=slipped, metadata=metadata)
