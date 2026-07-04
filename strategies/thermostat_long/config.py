"""Thermostat long strategy configuration (parameters only).

Pure dataclass: no Nautilus, no pandas, no feature_engine / strategy_framework
imports. Ported from the TradeBlazer ``Thermostat_L`` system — the long mirror of
``Thermostat_S``. A Choppy Market Index (CMI) splits the market into a *swing*
(ranging) regime and a *trend* regime; the swing regime trades an opening-range
ATR breakout, the trend regime trades a Bollinger-band breakout, and each regime
has its own exits.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThermostatLongConfig:
    """User-facing parameters for the Thermostat long strategy."""

    swing_trend_switch: float = 20.0  # swingTrendSwitch: CMI < this == swing, else trend
    swing_prcnt1: float = 0.50        # swingPrcnt1: near-side ATR breakout fraction
    swing_prcnt2: float = 0.75        # swingPrcnt2: far-side ATR breakout fraction
    atr_length: int = 10              # atrLength: AvgTrueRange period
    bollinger_length: int = 50        # bollingerLengths: Bollinger mid/band period
    num_std_devs: float = 2.0         # numStdDevs: Bollinger band width in std devs
    trend_liq_length: int = 50        # trendLiqLength: trend-exit MA period
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None

    def __post_init__(self) -> None:
        for name in ("atr_length", "bollinger_length", "trend_liq_length"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0.")
        for name in ("swing_prcnt1", "swing_prcnt2", "num_std_devs", "swing_trend_switch"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0.")
