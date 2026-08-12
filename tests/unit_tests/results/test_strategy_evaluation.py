import math

from results.strategy_evaluation import (
    build_additive_strategy_evaluation_from_columns,
    build_strategy_evaluation,
    signed_break_even_bps,
    validate_strategy_evaluation,
)
from strategy_framework.execution.reports import FillRecord


def _fill(ts: int, side: str, quantity: float, price: float) -> FillRecord:
    return FillRecord(
        instrument_id="BTCUSDT-PERP.BINANCE",
        side=side,
        quantity=quantity,
        price=price,
        event_time_ns=ts,
    )


def test_evaluation_uses_actual_position_and_preserves_negative_break_even() -> None:
    equity = [
        {
            "event_time_ns": 1,
            "close": 100_000.0,
            "position": 1.0,
            "position_leverage_pct": 100.0,
            "net_pnl": 0.0,
            "funding_pnl": 0.0,
        },
        {
            "event_time_ns": 2,
            "close": 90_000.0,
            "position": 1.0,
            "position_leverage_pct": 90.0,
            "net_pnl": -10_100.0,
            "funding_pnl": -100.0,
        },
    ]
    series, metrics = build_strategy_evaluation(
        equity,
        [_fill(1, "BUY", 1.0, 100_000.0)],
        initial_cash=100_000.0,
    )

    assert series[-1]["position"] == equity[-1]["position"]
    assert series[-1]["position_leverage_pct"] == 90.0
    assert metrics["included"]["final_return_1x"] == -0.101
    assert metrics["excluded"]["final_return_1x"] == -0.1
    assert metrics["included"]["turnover"] == 1.0
    assert math.isclose(metrics["included"]["break_even_bps"], -1010.0)
    assert metrics["included"]["max_drawdown"] < 0.0
    assert all(validate_strategy_evaluation(series, metrics).values())


def test_signed_break_even_reapplied_to_cost_formula_is_zero() -> None:
    total_return = 0.2
    turnover = 10.0
    bps = signed_break_even_bps(total_return, turnover)
    assert bps == 200.0
    assert math.isclose(total_return - turnover * bps / 10_000.0, 0.0, abs_tol=1e-15)


def test_saved_bar_evaluation_uses_executed_direction_and_exact_components() -> None:
    series, metrics = build_additive_strategy_evaluation_from_columns(
        event_time_ns=[1, 2, 3],
        trading_return=[0.0, -0.10, 0.02],
        funding_return=[0.0, -0.01, 0.0],
        turnover=[1.0, 2.0, 0.0],
        executed_direction=[1, -1, -1],
        max_points=2,
    )

    assert series[-1]["position_leverage_pct"] == -100.0
    assert math.isclose(metrics["included"]["final_return_1x"], -0.09)
    assert math.isclose(metrics["excluded"]["final_return_1x"], -0.08)
    assert metrics["included"]["turnover"] == 3.0
    assert math.isclose(metrics["included"]["break_even_bps"], -300.0)
    assert all(validate_strategy_evaluation(series, metrics).values())
