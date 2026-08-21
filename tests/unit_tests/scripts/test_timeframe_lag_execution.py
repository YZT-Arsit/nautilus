from __future__ import annotations

import numpy as np

from data_engine.events import BarEvent
from scripts.internal.run_all_strategy_timeframe_lag import MINUTE_NS
from scripts.internal.run_all_strategy_timeframe_lag import build_direction_variants
from scripts.internal.run_all_strategy_timeframe_lag import build_strategy_clock
from scripts.internal.run_all_strategy_timeframe_lag import execute_planned
from scripts.internal.run_all_strategy_timeframe_lag import execute_target
from scripts.internal.run_all_strategy_timeframe_lag import execution_bar
from scripts.internal.run_all_strategy_timeframe_lag import parse_cases
from scripts.internal.run_all_strategy_timeframe_lag import run_decision_lifecycle
from scripts.internal.run_all_strategy_timeframe_lag import validate_direction_variants
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.duration_lag import DurationLagTargetAdapter
from strategy_framework.execution.intents import PlannedSignal
from strategy_framework.execution.intents import TradeAction
from strategy_framework.modules import AtrHardStopModule


def test_execute_planned_returns_each_fill_once_without_mutating_external_ledger() -> None:
    event = BarEvent(
        close=100.0,
        open=100.0,
        high=101.0,
        low=99.0,
        volume=10.0,
        instrument_id="BTCUSDT-PERP.BINANCE",
        event_time_ns=1,
    )
    signal = PlannedSignal(
        "BUY",
        [TradeAction(side="BUY", quantity=1.0), TradeAction(side="BUY", quantity=2.0)],
    )
    simulator = IntentFillSimulator(default_price_field="open", allow_short=True)
    cumulative_fills = []

    new_fills = execute_planned(signal, event, simulator)

    assert len(new_fills) == 2
    assert cumulative_fills == []
    cumulative_fills.extend(new_fills)
    assert len(cumulative_fills) == 2
    assert simulator.report().total_fills == 2
    assert simulator.report().positions[0].quantity == 3.0


def test_execute_target_preserves_fractional_exposure() -> None:
    event = BarEvent(
        close=100.0,
        open=100.0,
        high=101.0,
        low=99.0,
        volume=10.0,
        instrument_id="BTCUSDT-PERP.BINANCE",
        event_time_ns=1,
    )
    simulator = IntentFillSimulator(default_price_field="open", allow_short=True)
    fills = []
    quantity, new_fills = execute_target(0.5, 0.0, event, simulator, fills)
    assert quantity == 0.5
    assert len(new_fills) == len(fills) == 1


def test_bar_frequency_and_physical_lag_are_independent() -> None:
    assert parse_cases(["1:0", "1:1", "5:0", "5:1", "10m:1m"]) == (
        ("1m", 0),
        ("1m", 1),
        ("5m", 0),
        ("5m", 1),
        ("10m", 1),
    )


def test_n_minute_clock_emits_only_at_completed_boundary_and_lag_is_separate() -> None:
    bars = [
        BarEvent(
            close=100.0 + index,
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            volume=1.0,
            instrument_id="BTCUSDT-PERP.BINANCE",
            event_time_ns=index * MINUTE_NS,
        )
        for index in range(10)
    ]
    clock = build_strategy_clock(bars, "5m")
    assert [bar.event_time_ns for bar in clock] == [5 * MINUTE_NS, 10 * MINUTE_NS]
    assert clock[0].open == 100.0 and clock[0].close == 104.0
    # A completed 5m decision plus an independent physical 1m lag fills at 6m.
    fill = execution_bar(
        bars,
        np.array([bar.event_time_ns for bar in bars]),
        clock[0].event_time_ns + MINUTE_NS,
    )
    assert fill is bars[6]


def test_tick_five_second_lag_uses_first_real_event_after_threshold() -> None:
    adapter = DurationLagTargetAdapter(lag_ns=5_000_000_000, notional=100_000.0)
    simulator = IntentFillSimulator(default_price_field="price", allow_short=True)

    class Tick:
        instrument_id = "BTCUSDT-PERP.BINANCE"
        price = 50_000.0

        def __init__(self, event_time_ns: int) -> None:
            self.event_time_ns = event_time_ns

    adapter.schedule(Tick(127_000_000), "BUY")
    assert adapter.on_market_event(Tick(5_126_000_000), simulator.on_intent) == []
    attempts = adapter.on_market_event(Tick(5_381_000_000), simulator.on_intent)

    assert len(attempts) == 1
    assert attempts[0].target.due_time_ns == 5_127_000_000
    assert attempts[0].fill_time_ns == 5_381_000_000
    assert attempts[0].observed_lag_ns == 5_254_000_000


def test_required_direction_variants_are_exact_filters_and_sign_inverse() -> None:
    original = np.array([0, 1, 1, 0, -1, -1, 1, 0], dtype=np.int8)

    variants = build_direction_variants(original)
    validation = validate_direction_variants(original, variants)

    np.testing.assert_array_equal(variants["normal"], original)
    np.testing.assert_array_equal(variants["long_only"], [0, 1, 1, 0, 0, 0, 1, 0])
    np.testing.assert_array_equal(variants["short_only"], [0, 0, 0, 0, -1, -1, 0, 0])
    np.testing.assert_array_equal(variants["strict_reverse"], -original)
    assert all(row["direction_validation_passed"] for row in validation)
    assert max(row["max_direction_residual"] for row in validation) == 0.0


def test_optional_module_hook_is_baseline_invariant_when_disabled_and_fill_based() -> None:
    prices = [100.0] * 30 + [100.0 + i for i in range(20)] + [119.0, 118.0, 90.0] + [90.0] * 10
    bars = [
        BarEvent(
            close=price,
            open=price,
            high=price + 0.5,
            low=price - 0.5,
            volume=1.0,
            instrument_id="BTCUSDT-PERP.BINANCE",
            event_time_ns=index * MINUTE_NS,
        )
        for index, price in enumerate(prices)
    ]
    kwargs = dict(
        strategy_name="ma_crossover",
        source_config={"params": {"fast_window": 5, "slow_window": 20}},
        frequency="1m",
        lag_minutes=0,
        bars_1m=bars,
        strategy_bars=bars,
        end_exclusive_ns=(len(bars) + 1) * MINUTE_NS,
    )
    baseline_a, _, _ = run_decision_lifecycle(**kwargs)
    baseline_b, _, _ = run_decision_lifecycle(**kwargs)
    np.testing.assert_array_equal(baseline_a, baseline_b)
    with_stop, events, meta = run_decision_lifecycle(
        **kwargs,
        strategy_module=AtrHardStopModule("test_stop", 0.1),
    )
    assert meta["module_decision_count"] > 0
    assert any(row["module_id"] == "test_stop" for row in events)
    assert np.any(with_stop != baseline_a)
