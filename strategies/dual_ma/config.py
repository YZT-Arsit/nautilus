"""Dual-MA (stop-and-reverse) strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``DualMA`` system — an always-in-market
stop-and-reverse: go long when the fast SMA is above the slow SMA (previous bar),
reverse to short when it is below. Because it flips between long and short (never
flat after warmup) it sizes a reversing order and runs through the rich-plan path,
same as ``turtle_trader``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DualMaConfig:
    """User-facing parameters for the Dual-MA strategy."""

    fast_length: int = 5      # FastLength: fast SMA period
    slow_length: int = 20     # SlowLength: slow SMA period
    contract_unit: float = 1.0  # order size of one unit (reversal submits two units)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.fast_length <= 0 or self.slow_length <= 0:
            raise ValueError("fast_length and slow_length must be > 0.")
        if self.contract_unit <= 0:
            raise ValueError("contract_unit must be > 0.")
