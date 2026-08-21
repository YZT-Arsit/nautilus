from __future__ import annotations

import math

from results.trade_episode import build_de_risk_episodes


def test_episode_segmentation_splits_reversal_turnover() -> None:
    rows, summary = build_de_risk_episodes(
        event_time_ns=[1, 2, 3, 4],
        executed_position=[1.0, 1.0, -0.5, 0.0],
        turnover_increment=[1.0, 0.1, 1.5, 0.5],
        gross_return_increment=[0.0, 0.02, -0.01, 0.03],
        strategy="example",
        symbol="BTCUSDT",
        granularity="1m bar",
        lag="0m physical-time",
        premium_mode="excluded",
    )

    assert [row["completion_reason"] for row in rows] == ["reversal", "close"]
    assert all(row["strategy_id"] == "example" for row in rows)
    assert all(row["variant"] == "original" for row in rows)
    assert all(row["timeframe"] == "1m bar" for row in rows)
    # +1 -> -0.5 splits the 1.5 turnover as 1.0 close and 0.5 new entry.
    assert math.isclose(rows[0]["delta_turnover"], 2.1)
    assert math.isclose(rows[1]["delta_turnover"], 1.0)
    assert summary["open_unfinished_episode_count"] == 0
    assert abs(summary["turnover_reconciliation_residual"]) < 1e-12
    assert summary["maximum_break_even_residual"] < 1e-12


def test_episode_break_even_preserves_negative_sign() -> None:
    rows, _ = build_de_risk_episodes(
        event_time_ns=[1, 2],
        executed_position=[1.0, 0.0],
        turnover_increment=[1.0, 1.0],
        gross_return_increment=[0.0, -0.02],
        strategy="example",
        symbol="BTCUSDT",
        granularity="tick",
        lag="5s physical-time",
        premium_mode="included",
    )
    assert rows[0]["break_even_bps"] == -100.0


def test_unfinished_open_episode_is_not_forced_closed() -> None:
    rows, summary = build_de_risk_episodes(
        event_time_ns=[1, 2],
        executed_position=[1.0, 1.0],
        turnover_increment=[1.0, 0.2],
        gross_return_increment=[0.0, 0.01],
        strategy="example",
        symbol="BTCUSDT",
        granularity="tick",
        lag="0s physical-time",
        premium_mode="excluded",
    )
    assert rows == []
    assert summary["open_unfinished_episode_count"] == 1
    assert math.isclose(summary["open_episode_turnover"], 1.2)
