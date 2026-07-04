"""Reference Deviation System short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Reference_Deviation_System_S`` system — a
mean-deviation oscillator (RDV): the deviation of price from an MA is summed over
N bars and normalised by the summed absolute deviation, giving a -100..100
oscillator; short when RDV is strongly negative, cover when RDV crosses back
above zero.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceDeviationShortConfig:
    """User-facing parameters for the Reference Deviation System short strategy."""

    et_short: float = -5.0   # ETShort: RDV entry threshold (short when RDV < this)
    rma_len: int = 15        # RMALen: MA period and RDV summation window
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.rma_len <= 0:
            raise ValueError("rma_len must be > 0.")
