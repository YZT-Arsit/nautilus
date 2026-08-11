"""Explicit strategy registry.

Maps a short strategy name (used in configs and ``--strategy``) to its
:class:`StrategyPlugin`. Registration is **explicit** — to add a strategy,
import its ``PLUGIN`` and add one entry below. There is no auto-discovery.
"""
from __future__ import annotations

from strategies.adx_ma_channel_long import PLUGIN as ADX_MA_CHANNEL_LONG_PLUGIN
from strategies.adx_ma_channel_short import PLUGIN as ADX_MA_CHANNEL_SHORT_PLUGIN
from strategies.avg_channel_range_leader_long import PLUGIN as AVG_CHANNEL_RANGE_LEADER_LONG_PLUGIN
from strategies.avg_channel_range_leader_short import PLUGIN as AVG_CHANNEL_RANGE_LEADER_SHORT_PLUGIN
from strategies.bollinger_bandit_long import PLUGIN as BOLLINGER_BANDIT_LONG_PLUGIN
from strategies.bollinger_bandit_short import PLUGIN as BOLLINGER_BANDIT_SHORT_PLUGIN
from strategies.continuous_tick_ma import PLUGIN as CONTINUOUS_TICK_MA_PLUGIN
from strategies.displaced_boll_long import PLUGIN as DISPLACED_BOLL_LONG_PLUGIN
from strategies.displaced_boll_short import PLUGIN as DISPLACED_BOLL_SHORT_PLUGIN
from strategies.double_your_fun_long import PLUGIN as DOUBLE_YOUR_FUN_LONG_PLUGIN
from strategies.double_your_fun_short import PLUGIN as DOUBLE_YOUR_FUN_SHORT_PLUGIN
from strategies.dual_ma import PLUGIN as DUAL_MA_PLUGIN
from strategies.dynamic_breakout_long import PLUGIN as DYNAMIC_BREAKOUT_LONG_PLUGIN
from strategies.dynamic_breakout_short import PLUGIN as DYNAMIC_BREAKOUT_SHORT_PLUGIN
from strategies.escalator_long import PLUGIN as ESCALATOR_LONG_PLUGIN
from strategies.escalator_short import PLUGIN as ESCALATOR_SHORT_PLUGIN
from strategies.first_pullback_long import PLUGIN as FIRST_PULLBACK_LONG_PLUGIN
from strategies.first_pullback_short import PLUGIN as FIRST_PULLBACK_SHORT_PLUGIN
from strategies.four_ma_crossover_long import PLUGIN as FOUR_MA_CROSSOVER_LONG_PLUGIN
from strategies.four_ma_crossover_short import PLUGIN as FOUR_MA_CROSSOVER_SHORT_PLUGIN
from strategies.ghost_trader_long import PLUGIN as GHOST_TRADER_LONG_PLUGIN
from strategies.ghost_trader_short import PLUGIN as GHOST_TRADER_SHORT_PLUGIN
from strategies.going_in_style_long import PLUGIN as GOING_IN_STYLE_LONG_PLUGIN
from strategies.going_in_style_short import PLUGIN as GOING_IN_STYLE_SHORT_PLUGIN
from strategies.in_the_zone_long import PLUGIN as IN_THE_ZONE_LONG_PLUGIN
from strategies.in_the_zone_short import PLUGIN as IN_THE_ZONE_SHORT_PLUGIN
from strategies.jailbreak_long import PLUGIN as JAILBREAK_LONG_PLUGIN
from strategies.jailbreak_short import PLUGIN as JAILBREAK_SHORT_PLUGIN
from strategies.keltner_channel_long import PLUGIN as KELTNER_CHANNEL_LONG_PLUGIN
from strategies.keltner_channel_short import PLUGIN as KELTNER_CHANNEL_SHORT_PLUGIN
from strategies.king_keltner_long import PLUGIN as KING_KELTNER_LONG_PLUGIN
from strategies.king_keltner_short import PLUGIN as KING_KELTNER_SHORT_PLUGIN
from strategies.ma_crossover import PLUGIN as MA_CROSSOVER_PLUGIN
from strategies.ma_crossover_channel_long import PLUGIN as MA_CROSSOVER_CHANNEL_LONG_PLUGIN
from strategies.ma_crossover_channel_short import PLUGIN as MA_CROSSOVER_CHANNEL_SHORT_PLUGIN
from strategies.ma_sup_res_long import PLUGIN as MA_SUP_RES_LONG_PLUGIN
from strategies.ma_sup_res_short import PLUGIN as MA_SUP_RES_SHORT_PLUGIN
from strategies.no_hurry_long import PLUGIN as NO_HURRY_LONG_PLUGIN
from strategies.no_hurry_short import PLUGIN as NO_HURRY_SHORT_PLUGIN
from strategies.obv_revisited_long import PLUGIN as OBV_REVISITED_LONG_PLUGIN
from strategies.obv_revisited_short import PLUGIN as OBV_REVISITED_SHORT_PLUGIN
from strategies.open_close_histogram_long import PLUGIN as OPEN_CLOSE_HISTOGRAM_LONG_PLUGIN
from strategies.open_close_histogram_short import PLUGIN as OPEN_CLOSE_HISTOGRAM_SHORT_PLUGIN
from strategies.redrover_long import PLUGIN as REDROVER_LONG_PLUGIN
from strategies.redrover_short import PLUGIN as REDROVER_SHORT_PLUGIN
from strategies.reference_deviation_long import PLUGIN as REFERENCE_DEVIATION_LONG_PLUGIN
from strategies.reference_deviation_short import PLUGIN as REFERENCE_DEVIATION_SHORT_PLUGIN
from strategies.spread_channel_breakout_long import PLUGIN as SPREAD_CHANNEL_BREAKOUT_LONG_PLUGIN
from strategies.spread_channel_breakout_short import PLUGIN as SPREAD_CHANNEL_BREAKOUT_SHORT_PLUGIN
from strategies.superman_long import PLUGIN as SUPERMAN_LONG_PLUGIN
from strategies.superman_short import PLUGIN as SUPERMAN_SHORT_PLUGIN
from strategies.swinger_long import PLUGIN as SWINGER_LONG_PLUGIN
from strategies.swinger_short import PLUGIN as SWINGER_SHORT_PLUGIN
from strategies.thermostat_long import PLUGIN as THERMOSTAT_LONG_PLUGIN
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
    CONTINUOUS_TICK_MA_PLUGIN.name: CONTINUOUS_TICK_MA_PLUGIN,
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
    THERMOSTAT_LONG_PLUGIN.name: THERMOSTAT_LONG_PLUGIN,
    SWINGER_SHORT_PLUGIN.name: SWINGER_SHORT_PLUGIN,
    SWINGER_LONG_PLUGIN.name: SWINGER_LONG_PLUGIN,
    SUPERMAN_SHORT_PLUGIN.name: SUPERMAN_SHORT_PLUGIN,
    SUPERMAN_LONG_PLUGIN.name: SUPERMAN_LONG_PLUGIN,
    SPREAD_CHANNEL_BREAKOUT_SHORT_PLUGIN.name: SPREAD_CHANNEL_BREAKOUT_SHORT_PLUGIN,
    SPREAD_CHANNEL_BREAKOUT_LONG_PLUGIN.name: SPREAD_CHANNEL_BREAKOUT_LONG_PLUGIN,
    REFERENCE_DEVIATION_SHORT_PLUGIN.name: REFERENCE_DEVIATION_SHORT_PLUGIN,
    REFERENCE_DEVIATION_LONG_PLUGIN.name: REFERENCE_DEVIATION_LONG_PLUGIN,
    REDROVER_SHORT_PLUGIN.name: REDROVER_SHORT_PLUGIN,
    REDROVER_LONG_PLUGIN.name: REDROVER_LONG_PLUGIN,
    OPEN_CLOSE_HISTOGRAM_SHORT_PLUGIN.name: OPEN_CLOSE_HISTOGRAM_SHORT_PLUGIN,
    OPEN_CLOSE_HISTOGRAM_LONG_PLUGIN.name: OPEN_CLOSE_HISTOGRAM_LONG_PLUGIN,
    OBV_REVISITED_SHORT_PLUGIN.name: OBV_REVISITED_SHORT_PLUGIN,
    OBV_REVISITED_LONG_PLUGIN.name: OBV_REVISITED_LONG_PLUGIN,
    NO_HURRY_SHORT_PLUGIN.name: NO_HURRY_SHORT_PLUGIN,
    NO_HURRY_LONG_PLUGIN.name: NO_HURRY_LONG_PLUGIN,
    MA_SUP_RES_SHORT_PLUGIN.name: MA_SUP_RES_SHORT_PLUGIN,
    MA_SUP_RES_LONG_PLUGIN.name: MA_SUP_RES_LONG_PLUGIN,
    MA_CROSSOVER_CHANNEL_LONG_PLUGIN.name: MA_CROSSOVER_CHANNEL_LONG_PLUGIN,
    MA_CROSSOVER_CHANNEL_SHORT_PLUGIN.name: MA_CROSSOVER_CHANNEL_SHORT_PLUGIN,
    KING_KELTNER_SHORT_PLUGIN.name: KING_KELTNER_SHORT_PLUGIN,
    KING_KELTNER_LONG_PLUGIN.name: KING_KELTNER_LONG_PLUGIN,
    KELTNER_CHANNEL_SHORT_PLUGIN.name: KELTNER_CHANNEL_SHORT_PLUGIN,
    KELTNER_CHANNEL_LONG_PLUGIN.name: KELTNER_CHANNEL_LONG_PLUGIN,
    JAILBREAK_SHORT_PLUGIN.name: JAILBREAK_SHORT_PLUGIN,
    JAILBREAK_LONG_PLUGIN.name: JAILBREAK_LONG_PLUGIN,
    IN_THE_ZONE_SHORT_PLUGIN.name: IN_THE_ZONE_SHORT_PLUGIN,
    IN_THE_ZONE_LONG_PLUGIN.name: IN_THE_ZONE_LONG_PLUGIN,
    GOING_IN_STYLE_SHORT_PLUGIN.name: GOING_IN_STYLE_SHORT_PLUGIN,
    GOING_IN_STYLE_LONG_PLUGIN.name: GOING_IN_STYLE_LONG_PLUGIN,
    GHOST_TRADER_SHORT_PLUGIN.name: GHOST_TRADER_SHORT_PLUGIN,
    GHOST_TRADER_LONG_PLUGIN.name: GHOST_TRADER_LONG_PLUGIN,
    FOUR_MA_CROSSOVER_SHORT_PLUGIN.name: FOUR_MA_CROSSOVER_SHORT_PLUGIN,
    FOUR_MA_CROSSOVER_LONG_PLUGIN.name: FOUR_MA_CROSSOVER_LONG_PLUGIN,
    FIRST_PULLBACK_SHORT_PLUGIN.name: FIRST_PULLBACK_SHORT_PLUGIN,
    FIRST_PULLBACK_LONG_PLUGIN.name: FIRST_PULLBACK_LONG_PLUGIN,
    ESCALATOR_SHORT_PLUGIN.name: ESCALATOR_SHORT_PLUGIN,
    ESCALATOR_LONG_PLUGIN.name: ESCALATOR_LONG_PLUGIN,
    DYNAMIC_BREAKOUT_SHORT_PLUGIN.name: DYNAMIC_BREAKOUT_SHORT_PLUGIN,
    DYNAMIC_BREAKOUT_LONG_PLUGIN.name: DYNAMIC_BREAKOUT_LONG_PLUGIN,
    DUAL_MA_PLUGIN.name: DUAL_MA_PLUGIN,
    DOUBLE_YOUR_FUN_SHORT_PLUGIN.name: DOUBLE_YOUR_FUN_SHORT_PLUGIN,
    DOUBLE_YOUR_FUN_LONG_PLUGIN.name: DOUBLE_YOUR_FUN_LONG_PLUGIN,
    DISPLACED_BOLL_SHORT_PLUGIN.name: DISPLACED_BOLL_SHORT_PLUGIN,
    DISPLACED_BOLL_LONG_PLUGIN.name: DISPLACED_BOLL_LONG_PLUGIN,
    BOLLINGER_BANDIT_SHORT_PLUGIN.name: BOLLINGER_BANDIT_SHORT_PLUGIN,
    BOLLINGER_BANDIT_LONG_PLUGIN.name: BOLLINGER_BANDIT_LONG_PLUGIN,
    AVG_CHANNEL_RANGE_LEADER_SHORT_PLUGIN.name: AVG_CHANNEL_RANGE_LEADER_SHORT_PLUGIN,
    AVG_CHANNEL_RANGE_LEADER_LONG_PLUGIN.name: AVG_CHANNEL_RANGE_LEADER_LONG_PLUGIN,
    ADX_MA_CHANNEL_SHORT_PLUGIN.name: ADX_MA_CHANNEL_SHORT_PLUGIN,
    ADX_MA_CHANNEL_LONG_PLUGIN.name: ADX_MA_CHANNEL_LONG_PLUGIN,
}


def get_entry(name: str) -> StrategyPlugin:
    """Look up a strategy plugin by name, with a helpful error listing valid names."""
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown strategy {name!r}. Registered strategies: {valid}") from None
