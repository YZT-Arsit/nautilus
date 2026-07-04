"""Keltner Channel short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``KeltnerChannel_S`` system — a Keltner
channel around an SMA (mid +/- Constt * ATR). A close crossing below the lower
band arms a short trigger ``sellN`` bars ahead at ``Low - ChanPcnt * (ChanRng)``;
a break of that trigger opens the short. Exits on a close back above the mid, or
on a break above the recent N-bar high.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeltnerChannelShortConfig:
    """User-facing parameters for the Keltner Channel short strategy."""

    length: int = 10          # length: SMA / ATR period for the channel
    constt: float = 1.2       # Constt: channel-width ATR multiple
    chan_pcnt: float = 0.5    # ChanPcnt: trigger offset as a fraction of the half-channel width
    sell_n: int = 5           # sellN: trigger stays live for this many bars after the cross
    stop_n: int = 4           # stopN: highest-high lookback for the protective stop
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("length must be > 0.")
        if self.stop_n <= 0:
            raise ValueError("stop_n must be > 0.")
        if self.sell_n < 0:
            raise ValueError("sell_n must be >= 0.")
