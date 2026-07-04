"""Spread Channel Breakout short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``SpreadChannelBreakout_S`` system — a
channel-breakout on the *spread* between two instruments
(``Data0*Factor0 - Data1*Factor1``): short the spread on a downside channel
breakout, reverse/stop on the upside channel.

Two-leg note: the TradeBlazer original constructs the spread bar from two data
series (``Lots0``/``Lots1`` and each leg's contract unit / big-point value). The
current single-symbol runner has no two-leg spread source, so this engine runs
the identical channel-breakout maths on **one** OHLC series (its open/close taken
as the spread's open/close). ``lots0`` / ``lots1`` are kept for fidelity and
future two-leg wiring; they are **not** used by the single-series engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpreadChannelBreakoutShortConfig:
    """User-facing parameters for the Spread Channel Breakout short strategy."""

    length: int = 20        # Length: breakout-channel period
    stop_len: int = 10      # StopLen: stop-channel period
    lots0: float = 1.0      # Lots0: leg-A size (two-leg spread construction; unused single-series)
    lots1: float = 1.0      # Lots1: leg-B size (two-leg spread construction; unused single-series)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("length", "stop_len"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
