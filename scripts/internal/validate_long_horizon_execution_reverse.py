#!/usr/bin/env python3
"""Focused validation for the canonical five-year execution/reverse package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    args = parser.parse_args()
    root, delivery = args.research_root, args.delivery_root

    freeze = json.loads((root / "long_horizon_window_freeze.json").read_text(encoding="utf-8-sig"))
    recorded = (root / "long_horizon_window_freeze.sha256").read_text().split()[0]
    assert sha256(root / "long_horizon_window_freeze.json") == recorded
    assert freeze["canonical_5y_start"] == "2021-07-01"
    assert freeze["canonical_5y_end_exclusive"] == "2026-07-01"
    assert freeze["number_of_calendar_days"] == 1826

    tick = pd.read_csv(root / "canonical_trade_tick_index_manifest.csv")
    assert len(tick) == 1826 and tick.minute_index_rows.sum() == 2_629_440
    assert tick.validation_status.eq("PASSED").all()
    assert tick.unresolved_boundaries.eq(0).all()
    assert tick.date.iloc[0] == "2021-07-01" and tick.date.iloc[-1] == "2026-06-30"

    availability = pd.read_csv(root / "long_horizon_maker_data_availability.csv")
    assert len(availability) == 18
    assert not availability.groupby("symbol").full_window_complete.all().any()
    books = availability[availability.data_type.eq("bookTicker")]
    assert books.available_required_days.eq(320).all()
    provenance = pd.read_csv(delivery / "data_provenance/long_horizon_maker_data.csv")
    assert len(provenance) == 45
    assert provenance.groupby("symbol").size().eq(5).all()
    assert provenance.ingestion_status.eq("NOT_INGESTED_FULL_WINDOW_DATA_UNAVAILABLE").all()

    all_cases = pd.read_csv(root / "selection/long_horizon_first_tick_all_cases.csv")
    selected = pd.read_csv(root / "selection/long_horizon_first_tick_selected_cases.csv")
    candidates = pd.read_csv(root / "selection/long_horizon_negative_reverse_candidates.csv")
    assert len(all_cases) == 993 and all_cases.strategy_id.nunique() == 331
    assert all_cases.n_daily_observations.eq(1826).all()
    assert all_cases.loc[~all_cases.Signed_BE_defined.astype(bool), "Turnover_FIRST_TICK"].eq(0).all()
    expected_selected = ((all_cases.timeframe.eq("1m") & all_cases.Sharpe_FIRST_TICK.abs().gt(1.5)) |
                         (all_cases.timeframe.isin(["10m", "15m"]) & all_cases.Signed_BE_FIRST_TICK.abs().gt(10) & all_cases.Sharpe_FIRST_TICK.abs().gt(1.0)))
    assert expected_selected.equals(all_cases.selected.astype(bool))
    assert set(selected.strategy_id + "|" + selected.symbol + "|" + selected.timeframe) == set(
        all_cases.loc[expected_selected, "strategy_id"] + "|" + all_cases.loc[expected_selected, "symbol"] + "|" + all_cases.loc[expected_selected, "timeframe"]
    )
    expected_reverse = ((all_cases.timeframe.eq("1m") & all_cases.Return_FIRST_TICK.lt(0) & all_cases.Sharpe_FIRST_TICK.lt(-1.5)) |
                        (all_cases.timeframe.isin(["10m", "15m"]) & all_cases.Return_FIRST_TICK.lt(0) & all_cases.Sharpe_FIRST_TICK.lt(-1.0) & all_cases.Signed_BE_FIRST_TICK.lt(-10)))
    assert len(candidates) == int(expected_reverse.sum())
    selection_freeze = json.loads((root / "selection/selection_freeze.json").read_text(encoding="utf-8-sig"))
    assert sha256(root / "selection/long_horizon_first_tick_selected_cases.csv") == selection_freeze["selected_manifest_sha256"]
    assert sha256(root / "selection/long_horizon_negative_reverse_candidates.csv") == selection_freeze["reverse_manifest_sha256"]

    mode = pd.read_csv(delivery / "mode_comparison.csv")
    maker_rows = mode[mode["mode"].str.startswith("MAKER")]
    maker_statuses = set(maker_rows.status)
    assert maker_statuses <= {"LONG_HORIZON_MAKER_DATA_UNAVAILABLE", "NOT_SELECTED_AS_NEGATIVE_REVERSE_CANDIDATE"}
    assert "LONG_HORIZON_MAKER_DATA_UNAVAILABLE" in maker_statuses
    assert not maker_rows.status.eq("COMPLETED").any()
    reverse = pd.read_csv(delivery / "selection/long_horizon_exploratory_reverse_results.csv")
    assert len(reverse) == len(candidates)
    assert reverse.evidence_class.eq("LONG_HORIZON_EXPLORATORY_REVERSE").all()
    assert reverse.n_daily_observations.eq(1826).all()
    reverse_summaries = sorted((root / "reverse_cases").glob("symbol=*/timeframe=*/semantic=*/summary.json"))
    expected_physical = candidates.drop_duplicates(["semantic_group_id", "symbol", "timeframe"])
    assert len(reverse_summaries) == len(expected_physical)
    for path in reverse_summaries:
        item = json.loads(path.read_text(encoding="utf-8-sig"))
        assert item["status"] == "COMPLETED"
        assert item["strict_reverse_target_mismatch_count"] == 0
        assert item["evidence_class"] == "LONG_HORIZON_EXPLORATORY_REVERSE"
    yearly = pd.read_csv(delivery / "yearly_robustness/yearly_metrics.csv")
    assert yearly.groupby(["strategy_id", "symbol", "timeframe", "mode"]).size().eq(5).all()

    index = pd.read_csv(delivery / "strategy_index.csv")
    assert len(index) == selected.strategy_id.nunique()
    for row in index.itertuples(index=False):
        folder = delivery / Path(row.strategy_folder)
        assert (folder / "strategy_summary.csv").is_file()
        assert (folder / "01_FIRST_TICK_NORMAL").is_dir()
        assert (folder / "02_MAKER_NORMAL").is_dir()
        assert (folder / "03_FIRST_TICK_REVERSE").is_dir()
        assert (folder / "04_MAKER_REVERSE").is_dir()
    assert len(list((delivery / "strategies").glob("*/01_FIRST_TICK_NORMAL/*.png"))) == len(selected)
    assert len(list((delivery / "strategies").glob("*/03_FIRST_TICK_REVERSE/*.png"))) == len(candidates)
    validation = json.loads((delivery / "validation_summary.json").read_text())
    assert validation["one_month_sharpe_used_for_selection"] is False
    assert validation["maker_cases"] == 0
    assert validation["strict_reverse_target_mismatch_count"] == 0
    print(json.dumps({"status": "PASSED", "assertions": 30}, indent=2))


if __name__ == "__main__":
    main()
