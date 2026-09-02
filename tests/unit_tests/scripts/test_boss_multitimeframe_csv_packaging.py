from __future__ import annotations

import pandas as pd

from scripts.internal.package_boss_multitimeframe_csv_results import (
    add_joint_positive_counts,
    normalize_master_delivery_schema,
    shortlist,
    strategy_summary,
    symbol_summary,
    timeframe_summary,
)


def fixture_master() -> pd.DataFrame:
    rows = []
    for strategy in ("s1", "s2"):
        for symbol in ("BTCUSDT", "ETHUSDT"):
            for timeframe in ("1m", "5m", "10m", "15m"):
                positive = strategy == "s1"
                rows.append(
                    {
                        "strategy_id": strategy,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "Return_fee0": 0.1 if positive else -0.1,
                        "Return_5bp": 0.05 if positive else -0.2,
                        "Turnover_raw": 10.0,
                        "Turnover_pct": 1_000.0,
                        "BE_bps": 100.0 if positive else -100.0,
                        "MDD": -0.2,
                        "nonflat_fraction": 0.95 if positive else 0.5,
                        "long_fraction": 0.5,
                        "short_fraction": 0.45,
                        "flat_fraction": 0.05,
                        "median_holding_duration_seconds": 600.0,
                    }
                )
    return pd.DataFrame(rows)


def test_summary_tables_reconcile_joint_positive_counts() -> None:
    master = fixture_master()
    strategy = add_joint_positive_counts(strategy_summary(master), master)
    assert len(strategy) == 8
    assert set(strategy[strategy.strategy_id == "s1"].positive_Return_BE_symbols) == {2}
    symbol = symbol_summary(master)
    assert len(symbol) == 8
    assert set(symbol.Return_BE_positive_count) == {1}
    timeframe = timeframe_summary(master)
    assert list(timeframe.timeframe) == ["1m", "5m", "10m", "15m"]
    assert set(timeframe.Return_BE_positive) == {2}


def test_shortlist_prioritizes_15m_and_preserves_transparent_reasons() -> None:
    master = fixture_master()
    strategy = add_joint_positive_counts(strategy_summary(master), master)
    result = shortlist(master, strategy)
    assert result.iloc[0].timeframe == "15m"
    assert "MULTI_SYMBOL_POSITIVE" in result.iloc[0].shortlist_reason
    assert "FIVE_BP_SURVIVOR" in result.iloc[0].shortlist_reason
    assert set(result.strategy_id) == {"s1"}


def test_master_delivery_aliases_preserve_source_values() -> None:
    master = fixture_master().assign(
        episode_count=3,
        p90_holding_duration_seconds=900.0,
        first_tick_wait_p95_ms=12.0,
    )
    result = normalize_master_delivery_schema(master)
    assert result.Signed_BE_bps.equals(result.BE_bps)
    assert result.completed_episode_count.equals(result.episode_count)
    assert result.median_holding_duration.equals(result.median_holding_duration_seconds)
    assert result.P90_holding_duration.equals(result.p90_holding_duration_seconds)
    assert result.first_tick_wait_P95_ms.equals(result.first_tick_wait_p95_ms)
