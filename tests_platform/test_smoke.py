"""Smoke tests for the reorganized active path (registry + run loop)."""
from run_strategy import run_config
from strategy_framework.registry import STRATEGY_REGISTRY, get_entry


def test_registry_has_expected_strategies():
    assert set(STRATEGY_REGISTRY) == {
        "ma_crossover",
        "vwm_short",
        "vwm_long",
        "trend_breakout_atr",
        "turtle_trader",
        "trendscore_short",
        "trendscore_long",
        "traffic_jam_short",
        "traffic_jam_long",
        "trading_range_breakout_short",
        "trading_range_breakout_long",
        "three_ema_crossover_short",
        "three_ema_crossover_long",
        "thermostat_short",
        "thermostat_long",
        "swinger_short",
        "swinger_long",
        "superman_short",
        "superman_long",
        "spread_channel_breakout_short",
        "spread_channel_breakout_long",
        "reference_deviation_short",
        "reference_deviation_long",
        "redrover_short",
        "redrover_long",
        "open_close_histogram_short",
        "open_close_histogram_long",
        "obv_revisited_short",
        "obv_revisited_long",
        "no_hurry_short",
        "no_hurry_long",
        "ma_sup_res_short",
        "ma_sup_res_long",
        "ma_crossover_channel_long",
        "ma_crossover_channel_short",
        "king_keltner_short",
        "king_keltner_long",
        "keltner_channel_short",
        "keltner_channel_long",
        "jailbreak_short",
        "jailbreak_long",
        "in_the_zone_short",
        "in_the_zone_long",
        "going_in_style_short",
        "going_in_style_long",
        "ghost_trader_short",
        "ghost_trader_long",
        "four_ma_crossover_short",
        "four_ma_crossover_long",
        "first_pullback_short",
        "first_pullback_long",
        "escalator_short",
        "escalator_long",
        "dynamic_breakout_short",
        "dynamic_breakout_long",
        "dual_ma",
        "double_your_fun_short",
        "double_your_fun_long",
        "displaced_boll_short",
        "displaced_boll_long",
        "bollinger_bandit_short",
        "bollinger_bandit_long",
        "avg_channel_range_leader_short",
        "avg_channel_range_leader_long",
    }


def test_get_entry_unknown_raises():
    try:
        get_entry("does_not_exist")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown strategy")


def _synthetic_cfg(strategy: str) -> dict:
    return {
        "strategy": strategy,
        "params": {},
        "data": {"mode": "synthetic", "warmup_bars": 20, "live_bars": 60},
        "output": {"print_table": False},
    }


def test_ma_crossover_synthetic_runs():
    # No execution backend -> run_config returns [] but must drive the full
    # data -> features -> signals loop without raising.
    assert run_config(_synthetic_cfg("ma_crossover")) == []


def test_vwm_short_synthetic_runs():
    assert run_config(_synthetic_cfg("vwm_short")) == []


def test_vwm_long_synthetic_runs():
    assert run_config(_synthetic_cfg("vwm_long")) == []


def test_turtle_trader_synthetic_runs():
    assert run_config(_synthetic_cfg("turtle_trader")) == []


def test_trendscore_short_synthetic_runs():
    assert run_config(_synthetic_cfg("trendscore_short")) == []


def test_trendscore_long_synthetic_runs():
    assert run_config(_synthetic_cfg("trendscore_long")) == []


def test_traffic_jam_short_synthetic_runs():
    assert run_config(_synthetic_cfg("traffic_jam_short")) == []


def test_traffic_jam_long_synthetic_runs():
    assert run_config(_synthetic_cfg("traffic_jam_long")) == []


def test_trading_range_breakout_short_synthetic_runs():
    assert run_config(_synthetic_cfg("trading_range_breakout_short")) == []


def test_trading_range_breakout_long_synthetic_runs():
    assert run_config(_synthetic_cfg("trading_range_breakout_long")) == []


def test_three_ema_crossover_short_synthetic_runs():
    assert run_config(_synthetic_cfg("three_ema_crossover_short")) == []


def test_three_ema_crossover_long_synthetic_runs():
    assert run_config(_synthetic_cfg("three_ema_crossover_long")) == []


def test_thermostat_short_synthetic_runs():
    assert run_config(_synthetic_cfg("thermostat_short")) == []


def test_thermostat_long_synthetic_runs():
    assert run_config(_synthetic_cfg("thermostat_long")) == []


def test_swinger_short_synthetic_runs():
    assert run_config(_synthetic_cfg("swinger_short")) == []


def test_swinger_long_synthetic_runs():
    assert run_config(_synthetic_cfg("swinger_long")) == []


def test_superman_short_synthetic_runs():
    assert run_config(_synthetic_cfg("superman_short")) == []


def test_superman_long_synthetic_runs():
    assert run_config(_synthetic_cfg("superman_long")) == []


def test_spread_channel_breakout_short_synthetic_runs():
    assert run_config(_synthetic_cfg("spread_channel_breakout_short")) == []


def test_spread_channel_breakout_long_synthetic_runs():
    assert run_config(_synthetic_cfg("spread_channel_breakout_long")) == []


def test_reference_deviation_short_synthetic_runs():
    assert run_config(_synthetic_cfg("reference_deviation_short")) == []


def test_reference_deviation_long_synthetic_runs():
    assert run_config(_synthetic_cfg("reference_deviation_long")) == []


def test_redrover_short_synthetic_runs():
    assert run_config(_synthetic_cfg("redrover_short")) == []


def test_redrover_long_synthetic_runs():
    assert run_config(_synthetic_cfg("redrover_long")) == []


def test_open_close_histogram_short_synthetic_runs():
    assert run_config(_synthetic_cfg("open_close_histogram_short")) == []


def test_open_close_histogram_long_synthetic_runs():
    assert run_config(_synthetic_cfg("open_close_histogram_long")) == []


def test_obv_revisited_short_synthetic_runs():
    assert run_config(_synthetic_cfg("obv_revisited_short")) == []


def test_obv_revisited_long_synthetic_runs():
    assert run_config(_synthetic_cfg("obv_revisited_long")) == []


def test_no_hurry_short_synthetic_runs():
    assert run_config(_synthetic_cfg("no_hurry_short")) == []


def test_no_hurry_long_synthetic_runs():
    assert run_config(_synthetic_cfg("no_hurry_long")) == []


def test_ma_sup_res_short_synthetic_runs():
    assert run_config(_synthetic_cfg("ma_sup_res_short")) == []


def test_ma_sup_res_long_synthetic_runs():
    assert run_config(_synthetic_cfg("ma_sup_res_long")) == []


def test_ma_crossover_channel_long_synthetic_runs():
    assert run_config(_synthetic_cfg("ma_crossover_channel_long")) == []


def test_ma_crossover_channel_short_synthetic_runs():
    assert run_config(_synthetic_cfg("ma_crossover_channel_short")) == []


def test_king_keltner_short_synthetic_runs():
    assert run_config(_synthetic_cfg("king_keltner_short")) == []


def test_king_keltner_long_synthetic_runs():
    assert run_config(_synthetic_cfg("king_keltner_long")) == []


def test_keltner_channel_short_synthetic_runs():
    assert run_config(_synthetic_cfg("keltner_channel_short")) == []


def test_keltner_channel_long_synthetic_runs():
    assert run_config(_synthetic_cfg("keltner_channel_long")) == []


def test_jailbreak_short_synthetic_runs():
    assert run_config(_synthetic_cfg("jailbreak_short")) == []


def test_jailbreak_long_synthetic_runs():
    assert run_config(_synthetic_cfg("jailbreak_long")) == []


def test_in_the_zone_short_synthetic_runs():
    assert run_config(_synthetic_cfg("in_the_zone_short")) == []


def test_in_the_zone_long_synthetic_runs():
    assert run_config(_synthetic_cfg("in_the_zone_long")) == []


def test_going_in_style_short_synthetic_runs():
    assert run_config(_synthetic_cfg("going_in_style_short")) == []


def test_going_in_style_long_synthetic_runs():
    assert run_config(_synthetic_cfg("going_in_style_long")) == []


def test_ghost_trader_short_synthetic_runs():
    assert run_config(_synthetic_cfg("ghost_trader_short")) == []


def test_ghost_trader_long_synthetic_runs():
    assert run_config(_synthetic_cfg("ghost_trader_long")) == []


def test_four_ma_crossover_short_synthetic_runs():
    assert run_config(_synthetic_cfg("four_ma_crossover_short")) == []


def test_four_ma_crossover_long_synthetic_runs():
    assert run_config(_synthetic_cfg("four_ma_crossover_long")) == []


def test_first_pullback_short_synthetic_runs():
    assert run_config(_synthetic_cfg("first_pullback_short")) == []


def test_first_pullback_long_synthetic_runs():
    assert run_config(_synthetic_cfg("first_pullback_long")) == []


def test_escalator_short_synthetic_runs():
    assert run_config(_synthetic_cfg("escalator_short")) == []


def test_escalator_long_synthetic_runs():
    assert run_config(_synthetic_cfg("escalator_long")) == []


def test_dynamic_breakout_short_synthetic_runs():
    assert run_config(_synthetic_cfg("dynamic_breakout_short")) == []


def test_dynamic_breakout_long_synthetic_runs():
    assert run_config(_synthetic_cfg("dynamic_breakout_long")) == []


def test_dual_ma_synthetic_runs():
    assert run_config(_synthetic_cfg("dual_ma")) == []


def test_double_your_fun_short_synthetic_runs():
    assert run_config(_synthetic_cfg("double_your_fun_short")) == []


def test_double_your_fun_long_synthetic_runs():
    assert run_config(_synthetic_cfg("double_your_fun_long")) == []


def test_displaced_boll_short_synthetic_runs():
    assert run_config(_synthetic_cfg("displaced_boll_short")) == []


def test_displaced_boll_long_synthetic_runs():
    assert run_config(_synthetic_cfg("displaced_boll_long")) == []


def test_bollinger_bandit_short_synthetic_runs():
    assert run_config(_synthetic_cfg("bollinger_bandit_short")) == []


def test_bollinger_bandit_long_synthetic_runs():
    assert run_config(_synthetic_cfg("bollinger_bandit_long")) == []


def test_avg_channel_range_leader_short_synthetic_runs():
    assert run_config(_synthetic_cfg("avg_channel_range_leader_short")) == []


def test_avg_channel_range_leader_long_synthetic_runs():
    assert run_config(_synthetic_cfg("avg_channel_range_leader_long")) == []
