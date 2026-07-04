"""Reference Deviation System long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Reference_Deviation_System_L`` system — the
long mirror of ``Reference_Deviation_System_S``. A mean-deviation oscillator
(RDV): the deviation of price from an MA is summed over N bars and normalised by
the summed absolute deviation, giving a -100..100 oscillator; long when RDV is
strongly positive, sell when RDV crosses back below zero.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceDeviationLongConfig:
    """User-facing parameters for the Reference Deviation System long strategy."""

    et_long: float = 5.0     # ETLong: RDV entry threshold (long when RDV > this)
    rma_len: int = 15        # RMALen: MA period and RDV summation window
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.rma_len <= 0:
            raise ValueError("rma_len must be > 0.")
