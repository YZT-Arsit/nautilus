"""ADX + MA-channel short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``ADXandMAChannelSys_S`` system — a short
setup that combines a rising Wilder ADX with price closing below the EMA of the
low, then shorts on a channel-width breakout target. Short-only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdxMaChannelShortConfig:
    """User-facing parameters for the ADX + MA-channel short strategy."""

    dmi_n: int = 14        # DMI_N: Wilder DMI/ADX smoothing length
    dmi_m: int = 30        # DMI_M: ADX-average period (only feeds oADXR; not used by the entry/exit)
    avg_len: int = 30      # AvgLen: EMA period of High / Low forming the channel
    entry_bar: int = 2     # EntryBar: bars the sell-setup stays valid for entry
    tick: float = 0.01     # MinMove * PriceScale: one price tick (minpoint)
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("dmi_n", "dmi_m", "avg_len", "entry_bar"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
