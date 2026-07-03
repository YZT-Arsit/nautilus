"""Explicit strategy registry.

Maps a short strategy name (used in configs and ``--strategy``) to its
:class:`StrategyPlugin`. Registration is **explicit** — to add a strategy,
import its ``PLUGIN`` and add one entry below. There is no auto-discovery.
"""
from __future__ import annotations

from strategies.ma_crossover import PLUGIN as MA_CROSSOVER_PLUGIN
from strategies.thermostat_short import PLUGIN as THERMOSTAT_SHORT_PLUGIN
from strategies.three_ema_crossover_long import PLUGIN as THREE_EMA_CROSSOVER_LONG_PLUGIN
from strategies.three_ema_crossover_short import PLUGIN as THREE_EMA_CROSSOVER_SHORT_PLUGIN
from strategies.traffic_jam_long import PLUGIN as TRAFFIC_JAM_LONG_PLUGIN
from strategies.traffic_jam_short import PLUGIN as TRAFFIC_JAM_SHORT_PLUGIN
from strategies.trading_range_breakout_long import PLUGIN as TRADING_RANGE_BREAKOUT_LONG_PLUGIN
from strategies.trading_range_breakout_short import PLUGIN as TRADING_RANGE_BREAKOUT_SHORT_PLUGIN
from strategies.trend_breakout_atr import PLUGIN as TREND_BREAKOUT_ATR_PLUGIN
from strategies.trendscore_long import PLUGIN as TRENDSCORE_LONG_PLUGIN
from strategies.trendscore_short import PLUGIN as TRENDSCORE_SHORT_PLUGIN
from strategies.turtle_trader import PLUGIN as TURTLE_TRADER_PLUGIN
from strategies.vwm_long import PLUGIN as VWM_LONG_PLUGIN
from strategies.vwm_short import PLUGIN as VWM_SHORT_PLUGIN
from strategy_framework.plugin import StrategyPlugin

STRATEGY_REGISTRY: dict[str, StrategyPlugin] = {
    MA_CROSSOVER_PLUGIN.name: MA_CROSSOVER_PLUGIN,
    VWM_SHORT_PLUGIN.name: VWM_SHORT_PLUGIN,
    VWM_LONG_PLUGIN.name: VWM_LONG_PLUGIN,
    TREND_BREAKOUT_ATR_PLUGIN.name: TREND_BREAKOUT_ATR_PLUGIN,
    TURTLE_TRADER_PLUGIN.name: TURTLE_TRADER_PLUGIN,
    TRENDSCORE_SHORT_PLUGIN.name: TRENDSCORE_SHORT_PLUGIN,
    TRENDSCORE_LONG_PLUGIN.name: TRENDSCORE_LONG_PLUGIN,
    TRAFFIC_JAM_SHORT_PLUGIN.name: TRAFFIC_JAM_SHORT_PLUGIN,
    TRAFFIC_JAM_LONG_PLUGIN.name: TRAFFIC_JAM_LONG_PLUGIN,
    TRADING_RANGE_BREAKOUT_SHORT_PLUGIN.name: TRADING_RANGE_BREAKOUT_SHORT_PLUGIN,
    TRADING_RANGE_BREAKOUT_LONG_PLUGIN.name: TRADING_RANGE_BREAKOUT_LONG_PLUGIN,
    THREE_EMA_CROSSOVER_SHORT_PLUGIN.name: THREE_EMA_CROSSOVER_SHORT_PLUGIN,
    THREE_EMA_CROSSOVER_LONG_PLUGIN.name: THREE_EMA_CROSSOVER_LONG_PLUGIN,
    THERMOSTAT_SHORT_PLUGIN.name: THERMOSTAT_SHORT_PLUGIN,
}


def get_entry(name: str) -> StrategyPlugin:
    """Look up a strategy plugin by name, with a helpful error listing valid names."""
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown strategy {name!r}. Registered strategies: {valid}") from None
