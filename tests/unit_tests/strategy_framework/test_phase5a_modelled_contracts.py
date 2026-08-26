from __future__ import annotations

import base64
import json

from feature_engine.api import FeatureSnapshot, FeatureValue
from strategies.workbook_parametric.config import WorkbookParametricConfig
from strategies.workbook_parametric.strategy import WorkbookParametricStrategy
from strategy_framework.semantic_contracts import (
    FalseBreakoutState,
    adx_strong,
    doji,
    indicator_strengthened,
    indicator_weakened,
    long_lower_shadow,
    long_upper_shadow,
    recent_confirmed_level,
    rolling_extreme,
    volume_contraction,
    volume_expansion,
)


def test_phase5a_scalar_modelled_contracts() -> None:
    assert volume_expansion(150, 100)
    assert not volume_expansion(149.999, 100)
    assert volume_contraction(70, 100)
    assert not volume_contraction(70.001, 100)
    assert rolling_extreme(10, list(range(10)), high=True)
    assert rolling_extreme(-1, list(range(10)), high=False)
    assert indicator_strengthened(1, 2)
    assert indicator_weakened(2, 1)
    assert adx_strong(25)
    assert not adx_strong(24.999)


def test_phase5a_candle_contracts() -> None:
    assert doji(100, 111, 99, 101.0)
    assert not doji(100, 111, 99, 102.0)
    assert long_upper_shadow(100, 104, 101)
    assert long_lower_shadow(100, 96, 99)


def test_phase5a_key_level_uses_only_recent_confirmed_values() -> None:
    levels = [None] * 58 + [98.0, None]
    assert recent_confirmed_level(levels, 60) == 98.0
    assert recent_confirmed_level([98.0] + [None] * 60, 60) is None


def test_phase5a_false_breakout_emits_only_on_completed_return_bar() -> None:
    state = FalseBreakoutState(direction=1, return_horizon=2)
    assert not state.update(101, 100)
    assert state.update(99, 100)
    state = FalseBreakoutState(direction=-1, return_horizon=2)
    assert not state.update(99, 100)
    assert state.update(101, 100)


def test_phase5a_declarative_cross_and_fill_free_position_decision() -> None:
    rule = {
        "schema_version": 1,
        "features": [
            {"kind": "bar", "name": "fast", "field": "close"},
            {"kind": "sma", "name": "slow", "window": 2},
        ],
        "long": {"op": "cross_above", "left": "fast", "right": "slow"},
        "short": {"op": "cross_below", "left": "fast", "right": "slow"},
        "exit_long": {"op": "cross_below", "left": "fast", "right": "slow"},
        "exit_short": {"op": "cross_above", "left": "fast", "right": "slow"},
    }
    encoded = base64.urlsafe_b64encode(json.dumps(rule).encode()).decode()
    strategy = WorkbookParametricStrategy(WorkbookParametricConfig(
        family="phase5a_declarative", semantic_provenance="MODELLED_BASELINE_INTERPRETATION",
        rule_spec_b64=encoded,
    ))
    def snapshot(ts: int, **values: float) -> FeatureSnapshot:
        return FeatureSnapshot(
            ts_event=ts, instrument_id="BTCUSDT-PERP.BINANCE",
            values={name: FeatureValue(name, value, True, source_event_time_ns=ts)
                    for name, value in values.items()},
        )
    first = snapshot(1, fast=1.0, slow=2.0)
    second = snapshot(2, fast=3.0, slow=2.0)
    assert strategy.on_snapshot(first) == "HOLD"
    signal = strategy.on_snapshot(second)
    assert str(signal) == "BUY"
    assert strategy.decision_position == 1.0
