"""Explicit strategy registry.

Maps a short strategy name (used in configs and ``--strategy``) to its
:class:`StrategyPlugin`. Registration is **explicit** — to add a strategy,
import its ``PLUGIN`` and add one entry below. There is no auto-discovery.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

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
from strategies.xlsx_s1_0002 import PLUGIN as XLSX_S1_0002_PLUGIN
from strategies.xlsx_s1_0003 import PLUGIN as XLSX_S1_0003_PLUGIN
from strategies.xlsx_s1_0004 import PLUGIN as XLSX_S1_0004_PLUGIN
from strategies.xlsx_s1_0005 import PLUGIN as XLSX_S1_0005_PLUGIN
from strategies.xlsx_s1_0006 import PLUGIN as XLSX_S1_0006_PLUGIN
from strategies.xlsx_s1_0007 import PLUGIN as XLSX_S1_0007_PLUGIN
from strategies.xlsx_s1_0010 import PLUGIN as XLSX_S1_0010_PLUGIN
from strategies.xlsx_s1_0012 import PLUGIN as XLSX_S1_0012_PLUGIN
from strategies.xlsx_s1_0016 import PLUGIN as XLSX_S1_0016_PLUGIN
from strategies.xlsx_s1_0017 import PLUGIN as XLSX_S1_0017_PLUGIN
from strategies.xlsx_s1_0019 import PLUGIN as XLSX_S1_0019_PLUGIN
from strategies.xlsx_s1_0020 import PLUGIN as XLSX_S1_0020_PLUGIN
from strategies.xlsx_s1_0024 import PLUGIN as XLSX_S1_0024_PLUGIN
from strategies.xlsx_s1_0029 import PLUGIN as XLSX_S1_0029_PLUGIN
from strategies.xlsx_s1_0027 import PLUGIN as XLSX_S1_0027_PLUGIN
from strategies.xlsx_s1_0025 import PLUGIN as XLSX_S1_0025_PLUGIN
from strategies.xlsx_s1_0026 import PLUGIN as XLSX_S1_0026_PLUGIN
from strategies.xlsx_s1_0033 import PLUGIN as XLSX_S1_0033_PLUGIN
from strategies.xlsx_s1_0034 import PLUGIN as XLSX_S1_0034_PLUGIN
from strategies.xlsx_s1_0038 import PLUGIN as XLSX_S1_0038_PLUGIN
from strategies.xlsx_s2_0230 import PLUGIN as XLSX_S2_0230_PLUGIN
from strategies.xlsx_s2_0042 import PLUGIN as XLSX_S2_0042_PLUGIN
from strategies.xlsx_s2_0277 import PLUGIN as XLSX_S2_0277_PLUGIN
from strategies.xlsx_s2_0363 import PLUGIN as XLSX_S2_0363_PLUGIN
from strategies.xlsx_s2_0560 import PLUGIN as XLSX_S2_0560_PLUGIN
from strategies.xlsx_s2_0708 import PLUGIN as XLSX_S2_0708_PLUGIN
from strategies.xlsx_s2_0737 import PLUGIN as XLSX_S2_0737_PLUGIN
from strategies.xlsx_s2_0879 import PLUGIN as XLSX_S2_0879_PLUGIN
from strategies.xlsx_s2_0017 import PLUGIN as XLSX_S2_0017_PLUGIN
from strategies.xlsx_s2_0316 import PLUGIN as XLSX_S2_0316_PLUGIN
from strategies.xlsx_s2_0432 import PLUGIN as XLSX_S2_0432_PLUGIN
from strategies.xlsx_s2_0513 import PLUGIN as XLSX_S2_0513_PLUGIN
from strategies.xlsx_s2_0665 import PLUGIN as XLSX_S2_0665_PLUGIN
from strategies.xlsx_s2_0842 import PLUGIN as XLSX_S2_0842_PLUGIN
from strategy_framework.plugin import StrategyPlugin

STRATEGY_REGISTRY: dict[str, StrategyPlugin] = {
    XLSX_S1_0002_PLUGIN.name: XLSX_S1_0002_PLUGIN,
    XLSX_S1_0003_PLUGIN.name: XLSX_S1_0003_PLUGIN,
    XLSX_S1_0004_PLUGIN.name: XLSX_S1_0004_PLUGIN,
    XLSX_S1_0005_PLUGIN.name: XLSX_S1_0005_PLUGIN,
    XLSX_S1_0006_PLUGIN.name: XLSX_S1_0006_PLUGIN,
    XLSX_S1_0007_PLUGIN.name: XLSX_S1_0007_PLUGIN,
    XLSX_S1_0010_PLUGIN.name: XLSX_S1_0010_PLUGIN,
    XLSX_S1_0012_PLUGIN.name: XLSX_S1_0012_PLUGIN,
    XLSX_S1_0016_PLUGIN.name: XLSX_S1_0016_PLUGIN,
    XLSX_S1_0017_PLUGIN.name: XLSX_S1_0017_PLUGIN,
    XLSX_S1_0019_PLUGIN.name: XLSX_S1_0019_PLUGIN,
    XLSX_S1_0020_PLUGIN.name: XLSX_S1_0020_PLUGIN,
    XLSX_S1_0024_PLUGIN.name: XLSX_S1_0024_PLUGIN,
    XLSX_S1_0029_PLUGIN.name: XLSX_S1_0029_PLUGIN,
    XLSX_S1_0027_PLUGIN.name: XLSX_S1_0027_PLUGIN,
    XLSX_S1_0025_PLUGIN.name: XLSX_S1_0025_PLUGIN,
    XLSX_S1_0026_PLUGIN.name: XLSX_S1_0026_PLUGIN,
    XLSX_S1_0033_PLUGIN.name: XLSX_S1_0033_PLUGIN,
    XLSX_S1_0034_PLUGIN.name: XLSX_S1_0034_PLUGIN,
    XLSX_S1_0038_PLUGIN.name: XLSX_S1_0038_PLUGIN,
    XLSX_S2_0230_PLUGIN.name: XLSX_S2_0230_PLUGIN,
    XLSX_S2_0042_PLUGIN.name: XLSX_S2_0042_PLUGIN,
    XLSX_S2_0277_PLUGIN.name: XLSX_S2_0277_PLUGIN,
    XLSX_S2_0363_PLUGIN.name: XLSX_S2_0363_PLUGIN,
    XLSX_S2_0560_PLUGIN.name: XLSX_S2_0560_PLUGIN,
    XLSX_S2_0708_PLUGIN.name: XLSX_S2_0708_PLUGIN,
    XLSX_S2_0737_PLUGIN.name: XLSX_S2_0737_PLUGIN,
    XLSX_S2_0879_PLUGIN.name: XLSX_S2_0879_PLUGIN,
    XLSX_S2_0017_PLUGIN.name: XLSX_S2_0017_PLUGIN,
    XLSX_S2_0316_PLUGIN.name: XLSX_S2_0316_PLUGIN,
    XLSX_S2_0432_PLUGIN.name: XLSX_S2_0432_PLUGIN,
    XLSX_S2_0513_PLUGIN.name: XLSX_S2_0513_PLUGIN,
    XLSX_S2_0665_PLUGIN.name: XLSX_S2_0665_PLUGIN,
    XLSX_S2_0842_PLUGIN.name: XLSX_S2_0842_PLUGIN,
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

# Phase 2.2B packages are explicitly enumerated by the compiled semantic plan.
# This is normal registry registration (not filesystem auto-discovery), and it
# keeps hundreds of generated import statements out of this source file.
_SEMANTIC_PLANS = (
    Path(__file__).resolve().parents[1] / "configs/semantic_contracts/workbook_phase2_2b_strategies.json",
    Path(__file__).resolve().parents[1] / "configs/semantic_contracts/workbook_phase2_2c_strategies.json",
    Path(__file__).resolve().parents[1] / "configs/semantic_contracts/workbook_phase2_3_strategies.json",
    Path(__file__).resolve().parents[1] / "configs/semantic_contracts/workbook_phase5a_strategies.json",
)
for _semantic_plan in _SEMANTIC_PLANS:
    if not _semantic_plan.is_file():
        continue
    for _strategy_id in sorted(json.loads(_semantic_plan.read_text(encoding="utf-8"))):
        _plugin = importlib.import_module(f"strategies.{_strategy_id}.plugin").PLUGIN
        if _plugin.name in STRATEGY_REGISTRY:
            raise ValueError(f"duplicate semantic strategy registration: {_plugin.name}")
        STRATEGY_REGISTRY[_plugin.name] = _plugin


def get_entry(name: str) -> StrategyPlugin:
    """Look up a strategy plugin by name, with a helpful error listing valid names."""
    try:
        return STRATEGY_REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(STRATEGY_REGISTRY)) or "(none registered)"
        raise KeyError(f"Unknown strategy {name!r}. Registered strategies: {valid}") from None
