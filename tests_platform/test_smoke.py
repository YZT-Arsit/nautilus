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
