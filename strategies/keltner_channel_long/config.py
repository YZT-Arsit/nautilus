"""Keltner Channel long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``KeltnerChannel_L`` system — the long mirror
of ``keltner_channel_short``: a Keltner channel around an SMA (mid +/- Constt *
ATR). A close crossing above the upper band arms a long trigger ``buyN`` bars
ahead at ``High + ChanPcnt * (ChanRng)``; a break of that trigger opens the long.
Exits on a close back below the mid, or on a break below the recent N-bar low.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeltnerChannelLongConfig:
    """User-facing parameters for the Keltner Channel long strategy."""

    length: int = 10          # length: SMA / ATR period for the channel
    constt: float = 1.2       # Constt: channel-width ATR multiple
    chan_pcnt: float = 0.5    # ChanPcnt: trigger offset as a fraction of the half-channel width
    buy_n: int = 5            # buyN: trigger stays live for this many bars after the cross
    stop_n: int = 4           # stopN: lowest-low lookback for the protective stop
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("length must be > 0.")
        if self.stop_n <= 0:
            raise ValueError("stop_n must be > 0.")
        if self.buy_n < 0:
            raise ValueError("buy_n must be >= 0.")
