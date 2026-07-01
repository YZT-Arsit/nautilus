"""Smoke tests for the reorganized active path (registry + run loop)."""
from run_strategy import run_config
from strategy_framework.registry import STRATEGY_REGISTRY, get_entry


def test_registry_has_expected_strategies():
    assert set(STRATEGY_REGISTRY) == {"ma_crossover", "vwm_short", "trend_breakout_atr"}


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
