"""Traffic Jam short strategy configuration (user-facing parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Traffic_Jam_S`` system — a short-only
counter-trend system that uses the ADX (from Wilder's DMI) to detect a ranging
market, then fades ``consec_bars`` consecutive up-closes with an ATR protective
stop and a time-based proactive exit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrafficJamShortConfig:
    """User-facing parameters for the Traffic Jam short strategy."""

    dmi_n: int = 14                        # DMI_N: DMI/ADX Wilder period
    dmi_m: int = 6                         # DMI_M: unused by this system (kept for parity)
    adx_level: float = 25.0                # ADXLevel: ADX below this == ranging market
    adx_lower_than_before: int = 3         # ADXLowThanBefore: ADX[1] must be < ADX[this+1]
    consec_bars: int = 3                   # ConsecBars: consecutive up-closes to fade
    atr_length: int = 10                   # ATRLength: protective-stop ATR period
    protect_stop_atr_multi: float = 0.5    # ProtectStopATRMulti: protective stop ATR multiple
    proactive_stop_bars: int = 10          # ProactiveStopBars: bars held before a time exit
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("dmi_n", "consec_bars", "atr_length", "proactive_stop_bars"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        if self.adx_lower_than_before < 0:
            raise ValueError("adx_lower_than_before must be >= 0.")
        if self.adx_level < 0:
            raise ValueError("adx_level must be >= 0.")
        if self.protect_stop_atr_multi < 0:
            raise ValueError("protect_stop_atr_multi must be >= 0.")
