from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts/internal/build_dynamic_breakout_cross_symbol_status.py"
)
SPEC = importlib.util.spec_from_file_location("cross_symbol_status", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_existing_repository_status_is_explicitly_partial(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    research = tmp_path / "research"
    delivery = tmp_path / "delivery"

    summary = MODULE.build(repo, research, delivery)

    assert summary["status"] == "PARTIAL"
    assert summary["normal_cases_reusable"] == 8
    assert summary["strict_reverse_cases_reusable"] == 4
    assert summary["total_cases_reusable"] == 12
    assert summary["total_cases_requested"] == 16
    assert summary["backtests_rerun"] == 0
    assert summary["market_data_downloads"] == 0
    assert summary["forward_paper_started"] is False

    cases = pd.read_csv(delivery / "dynamic_breakout_short_cross_symbol.csv")
    assert len(cases) == 16
    missing = cases[cases.case_status.eq("MISSING_RERUN")]
    assert set(missing.symbol) == {"ETHUSDT", "BNBUSDT", "ADAUSDT", "1000PEPEUSDT"}
    assert missing[["Return", "Sharpe", "Signed_BE", "MaxDD"]].isna().all().all()

    candidates = pd.read_csv(delivery / "next_forward_symbol_candidates.csv")
    assert set(candidates.symbol) == {"SOLUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT"}
    assert not candidates.forward_launch_authorized.astype(bool).any()
