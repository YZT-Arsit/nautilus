"""DoubleYourFun short strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``DoubleYourFun_S`` system — a displaced-MA
"double crossing" breakout: the close crosses under the displaced MA, back over,
then under again (down / up / down) within validity windows; the second down-cross
arms a break of that bar's low, and a break within a further window shorts. Exits
on the nearer of a reversal stop (back above the displaced MA) and an N-bar
trailing high.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoubleYourFunShortConfig:
    """User-facing parameters for the DoubleYourFun short strategy."""

    avg_length: int = 5        # AvgLength: MA period
    avg_displace: int = 5      # AvgDisplace: bars the MA is shifted back to form the DMA
    valid_bars1: int = 5       # ValidBars1: max gap (1st down-cross -> middle up-cross)
    valid_bars2: int = 5       # ValidBars2: max age of the middle up-cross at the 2nd down-cross
    valid_bars3: int = 5       # ValidBars3: bars the armed entry stays valid
    trail_stop_bars: int = 5   # TrailStopBars: high-channel period for the trailing stop
    tick: float = 0.01         # MinMove * PriceScale: one price tick
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("avg_length", "avg_displace", "trail_stop_bars"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        for name in ("valid_bars1", "valid_bars2", "valid_bars3"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0.")
        if self.tick < 0:
            raise ValueError("tick must be >= 0.")
