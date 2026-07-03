"""Explicit strategy registry.

Maps a short strategy name (used in configs and ``--strategy``) to its
:class:`StrategyPlugin`. Registration is **explicit** — to add a strategy,
import its ``PLUGIN`` and add one entry below. There is no auto-discovery.
"""
from __future__ import annotations

from strategies.ma_crossover import PLUGIN as MA_CROSSOVER_PLUGIN
from strategies.trend_breakout_atr import PLUGIN as TREND_BREAKOUT_ATR_PLUGIN
from strategies.vwm_long import PLUGIN as VWM_LONG_PLUGIN
from strategies.vwm_short import PLUGIN as VWM_SHORT_PLUGIN
from strategy_framework.plugin import StrategyPlugin

STRATEGY_REGISTRY: dict[str, StrategyPlugin] = {
    MA_CROSSOVER_PLUGIN.name: MA_CROSSOVER_PLUGIN,
    VWM_SHORT_PLUGIN.name: VWM_SHORT_PLUGIN,
    VWM_LONG_PLUGIN.name: VWM_LONG_PLUGIN,
    TREND_BREAKOUT_ATR_PLUGIN.name: TREND_BREAKOUT_ATR_PLUGIN,
}


def get_entry(name: str) -> StrategyPlugin:
    """Look up a strategy plugin by name, with a helpful error listing valid names."""
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown strategy {name!r}. Registered strategies: {valid}") from None
