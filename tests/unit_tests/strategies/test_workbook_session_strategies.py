from __future__ import annotations

from feature_engine.api import FeatureSnapshot, FeatureValue
from strategy_framework.execution.intents import PlannedSignal
from strategy_framework.registry import get_entry


def snapshot(**values: float | bool) -> FeatureSnapshot:
    return FeatureSnapshot(
        ts_event=1, instrument_id="BTCUSDT-PERP.BINANCE",
        values={name: FeatureValue(name, value, True) for name, value in values.items()},
    )


def common(**values: float | bool) -> dict[str, float | bool]:
    return {
        "workbook_close": 101.0, "workbook_session_vwap": 100.0,
        "workbook_session_start": 0.0, "workbook_session_entry_allowed": True,
        "workbook_session_flatten": False, **values,
    }


def test_session_ma_requires_completed_persistence_and_flattens_before_boundary() -> None:
    plugin = get_entry("xlsx_s1_0461")
    strategy = plugin.strategy_cls(plugin.config_cls())
    row = common(workbook_completed_ma=99.0, workbook_atr=10.0)
    assert strategy.on_snapshot(snapshot(**row)) == "HOLD"
    assert strategy.on_snapshot(snapshot(**row)) == "BUY"
    flat = strategy.on_snapshot(snapshot(**{**row, "workbook_session_flatten": True}))
    assert isinstance(flat, PlannedSignal) and flat == "EXIT"
    assert flat.actions[0].close_all


def test_session_roc_cross_entry_and_vwap_exit_are_source_directional() -> None:
    plugin = get_entry("xlsx_s1_0488")
    strategy = plugin.strategy_cls(plugin.config_cls())
    assert strategy.on_snapshot(snapshot(**common(workbook_roc=-0.01))) == "HOLD"
    assert strategy.on_snapshot(snapshot(**common(workbook_roc=0.01))) == "BUY"
    assert strategy.on_snapshot(snapshot(**common(
        workbook_close=99.0, workbook_session_vwap=100.0, workbook_roc=-0.01,
    ))) == "EXIT"


def test_session_volume_family_uses_15bar_mean_and_fill_independent_reduction_target() -> None:
    plugin = get_entry("xlsx_s1_0516")
    strategy = plugin.strategy_cls(plugin.config_cls())
    first = common(workbook_volume=9.0, workbook_volume_mean=10.0)
    second = common(workbook_volume=11.0, workbook_volume_mean=10.0)
    assert strategy.on_snapshot(snapshot(**first)) == "HOLD"
    assert strategy.on_snapshot(snapshot(**second)) == "BUY"
    reduction = strategy.on_snapshot(snapshot(**first))
    assert reduction == "SELL"
    assert reduction.actions[0].quantity == 0.5


def test_session_mtf_fractal_requires_same_timestamp_completed_confluence() -> None:
    plugin = get_entry("xlsx_s2_0659")
    strategy = plugin.strategy_cls(plugin.config_cls())
    values = common(**{
        "workbook_lower_fractal_5m": 1.0, "workbook_lower_fractal_15m": 1.0,
        "workbook_lower_fractal_30m": 1.0, "workbook_upper_fractal_5m": 0.0,
        "workbook_upper_fractal_15m": 0.0, "workbook_upper_fractal_30m": 0.0,
    })
    assert strategy.on_snapshot(snapshot(**values)) == "BUY"
    reverse = dict(values)
    for frame in (5, 15, 30):
        reverse[f"workbook_lower_fractal_{frame}m"] = 0.0
        reverse[f"workbook_upper_fractal_{frame}m"] = 1.0
    assert strategy.on_snapshot(snapshot(**reverse)) == "EXIT"


def test_session_strategy_cannot_reopen_on_midnight_boundary_snapshot() -> None:
    plugin = get_entry("xlsx_s1_0461")
    strategy = plugin.strategy_cls(plugin.config_cls())
    row = common(
        workbook_completed_ma=99.0, workbook_atr=10.0,
        workbook_session_entry_allowed=False,
    )
    assert strategy.on_snapshot(snapshot(**row)) == "HOLD"
    assert strategy.on_snapshot(snapshot(**row)) == "HOLD"
    assert strategy.decision_position == 0
