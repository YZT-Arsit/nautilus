from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.internal.build_boss_persistence_v2 import directional_persistence_metrics
from scripts.internal.build_boss_persistent_followup import (
    cross_symbol_matrix,
    shortlist,
    strategy_timeframe_summary,
    timeframe_effect,
)
from scripts.internal.run_boss_persistence_parameter_sensitivity import (
    admissible,
    config_hash,
    select_values,
)


MINUTE_NS = 60_000_000_000


def metrics_fixture() -> pd.DataFrame:
    rows = []
    for symbol, persistent in (("BTCUSDT", True), ("ETHUSDT", True), ("SOLUSDT", False)):
        for timeframe, run in (("1m", 12.0), ("5m", 24.0), ("10m", 36.0), ("15m", 48.0)):
            rows.append(
                {
                    "strategy_id": "s1", "symbol": symbol, "timeframe": timeframe,
                    "directionally_persistent": persistent,
                    "always_in_market": True,
                    "nonflat_fraction_v2": 0.95,
                    "median_directional_run_hours": run,
                    "P90_directional_run_hours": run * 2,
                    "sign_switches_per_day": 0.5,
                    "turnover_raw": 2.0,
                    "turnover_percent": 200.0,
                    "Return": 0.1,
                    "BE": 5.0,
                    "Return_5bp": 0.0,
                }
            )
    return pd.DataFrame(rows)


def test_strategy_timeframe_and_cross_symbol_aggregation() -> None:
    metrics = metrics_fixture()
    summary = strategy_timeframe_summary(metrics)
    assert len(summary) == 4
    assert set(summary.persistent_symbol_count) == {2}
    cross = cross_symbol_matrix(metrics, summary)
    row = cross[cross.timeframe == "15m"].iloc[0]
    assert row.BTCUSDT_persistent
    assert row.ETHUSDT_persistent
    assert not row.SOLUSDT_persistent
    assert row.persistent_symbol_count == 2


def test_timeframe_deltas_use_same_strategy_and_symbol() -> None:
    effect = timeframe_effect(metrics_fixture())
    assert len(effect) == 3
    assert np.allclose(effect.delta_10m_vs_1m_median_run_hours, 24.0)
    assert np.allclose(effect.delta_15m_vs_1m_Return, 0.0)


def test_shortlist_sort_is_deterministic_and_timeframe_first() -> None:
    summary = strategy_timeframe_summary(metrics_fixture())
    first = shortlist(summary)
    second = shortlist(summary.sample(frac=1.0, random_state=7))
    assert first[["strategy_id", "timeframe"]].equals(second[["strategy_id", "timeframe"]])
    assert first.timeframe.tolist() == ["15m", "10m", "5m", "1m"]


def test_fractional_positions_are_segmented_by_sign_and_physical_time() -> None:
    frame = pd.DataFrame(
        {
            "event_time_ns": [0, 2 * 1440 * MINUTE_NS - MINUTE_NS,
                              2 * 1440 * MINUTE_NS, 5 * 1440 * MINUTE_NS - MINUTE_NS],
            "executed_position": [0.25, 0.25, -0.4, -0.4],
        }
    )
    result = directional_persistence_metrics(frame)
    assert result["nonflat_fraction_v2"] == 1.0
    assert result["median_directional_run_duration"] == 2.5 * 86_400
    assert np.isclose(result["sign_switches_per_day"], 0.2)
    assert result["directionally_persistent"]


def test_parameter_values_are_bounded_and_single_variable_hash_changes() -> None:
    params = {"fast_window": 20, "slow_window": 60, "threshold": 2.0}
    values, source = select_values(
        members=["s"], parameter="fast_window", canonical=20,
        params=params, authorized={},
    )
    assert source == "PREDECLARED_BOUNDED_0.75X_1.25X"
    assert values == [15, 20, 25]
    assert all(admissible(params, "fast_window", value) for value in values)
    base = {"params": dict(params)}
    modified = {"params": {**params, "fast_window": 25}}
    assert config_hash(base) != config_hash(modified)
    changed = [key for key in params if params[key] != modified["params"][key]]
    assert changed == ["fast_window"]


def test_admissibility_enforces_order_constraints() -> None:
    params = {"fast_window": 20, "slow_window": 60, "lower_threshold": 30, "upper_threshold": 70}
    assert not admissible(params, "fast_window", 60)
    assert not admissible(params, "lower_threshold", 70)
    assert admissible(params, "slow_window", 75)
