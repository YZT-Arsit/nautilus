from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts/internal/build_dynamic_breakout_cross_symbol_status.py"
)
SPEC = importlib.util.spec_from_file_location("cross_symbol_status", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_existing_repository_builds_complete_replication_when_artifacts_present(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[3]
    required = (
        repo
        / "outputs/baseline_evaluation/dynamic_breakout_short_cross_symbol/reverse_cases"
        / "symbol=ETHUSDT/timeframe=1m/strategy=dynamic_breakout_short/summary.json"
    )
    if not required.is_file():
        pytest.skip("large server result artifacts are not stored in git")
    research = tmp_path / "research"
    delivery = tmp_path / "delivery"

    summary = MODULE.build(repo, research, delivery)

    assert summary["status"] == "PASSED"
    assert summary["evidence_class"] == "HISTORICAL_CROSS_SYMBOL_REPLICATION"
    assert summary["normal_cases_reusable"] == 8
    assert summary["strict_reverse_cases_reusable"] == 8
    assert summary["complete_symbol_pairs"] == 8
    assert summary["wide_paired_rows"] == 8
    assert summary["full_window_comparable_pairs"] == {
        "Return": 8,
        "Sharpe": 4,
        "BE": 8,
        "MaxDD": 4,
        "Avg_Daily_Turnover_pct": 8,
    }
    assert summary["strict_reverse_improves_metric"] == {"Return": 7, "Sharpe": 3, "BE": 7}
    assert summary["positive_full_window_return"] == {"NORMAL": 1, "STRICT_REVERSE": 7}
    assert summary["backtests_rerun_during_packaging"] == 0
    assert summary["market_data_downloads"] == 0
    assert summary["forward_launches"] == 0
    assert summary["forward_launch_authorized"] is False

    cases = pd.read_csv(delivery / "dynamic_breakout_short_cross_symbol.csv")
    assert len(cases) == 16
    assert cases.groupby("symbol").direction_variant.nunique().eq(2).all()
    old_reverse = cases[
        cases.symbol.isin({"SOLUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT"})
        & cases.direction_variant.eq("STRICT_REVERSE")
    ]
    assert old_reverse[["Sharpe", "MaxDD"]].isna().all().all()

    wide = pd.read_csv(delivery / "normal_vs_reverse_paired.csv")
    assert len(wide) == 8
    assert wide.symbol.nunique() == 8
    old_wide = wide[wide.symbol.isin({"SOLUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT"})]
    assert old_wide[["Delta_Sharpe", "Delta_MaxDD"]].isna().all().all()
    assert old_wide.Delta_Sharpe_status.eq("UNAVAILABLE_SOURCE_METRIC").all()
    assert old_wide.Delta_MaxDD_status.eq("UNAVAILABLE_SOURCE_METRIC").all()
    expected_turnover = (
        cases[cases.direction_variant.eq("NORMAL")]
        .set_index("symbol")
        .loc[wide.symbol, "Total_Turnover_raw"]
        .to_numpy()
        / wide.n_daily_observations
        * 100.0
    )
    assert wide.NORMAL_Avg_Daily_Turnover_pct.to_numpy() == pytest.approx(expected_turnover)

    candidates = pd.read_csv(delivery / "next_forward_symbol_candidates.csv")
    assert set(candidates.symbol) == set(MODULE.SYMBOLS)
    assert not candidates.forward_launch_authorized.astype(bool).any()

    yearly = pd.read_csv(delivery / "yearly_robustness.csv")
    assert len(yearly) == 32
    assert yearly.groupby(["symbol", "direction_variant"]).size().eq(2).all()
    assert (delivery / "cross_symbol_normal_vs_strict_reverse.png").is_file()
