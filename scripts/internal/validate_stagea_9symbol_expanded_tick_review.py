#!/usr/bin/env python3
"""Validate the expanded Stage-A delivery independently of its builder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()

    all_results = pd.read_csv(output / "all_1m10m15m_results.csv")
    qualifying = pd.read_csv(output / "qualifying_cases.csv")
    strategies = pd.read_csv(output / "qualifying_strategies.csv")
    index = pd.read_csv(output / "strategy_index.csv")
    scope = pd.read_csv(output / "strategy_scope.csv")
    origin = pd.read_csv(output / "source_origin_summary.csv")
    preview = pd.read_csv(output / "available_symbol_pool_preview.csv")
    validation = json.loads((output / "validation_summary.json").read_text(encoding="utf-8"))

    assert len(all_results) == 8937
    assert all_results.strategy_id.nunique() == 331
    assert set(all_results.source_origin) == {"WORKBOOK", "PRE_WORKBOOK"}
    assert set(all_results.timeframe) == {"1m", "10m", "15m"}
    assert all_results.symbol.nunique() == 9
    assert not all_results.duplicated(["strategy_id", "symbol", "timeframe"]).any()
    assert not any("5bp" in column.lower() for column in all_results.columns)

    expected_1m = all_results.timeframe.eq("1m") & all_results.Sharpe.abs().gt(1.5)
    expected_slow = (
        all_results.timeframe.isin(["10m", "15m"])
        & all_results.Signed_BE_bps.abs().gt(10)
        & all_results.Sharpe.abs().gt(1)
    )
    expected = all_results[expected_1m | expected_slow]
    actual_keys = set(map(tuple, qualifying[["strategy_id", "symbol", "timeframe"]].to_numpy()))
    expected_keys = set(map(tuple, expected[["strategy_id", "symbol", "timeframe"]].to_numpy()))
    assert actual_keys == expected_keys
    assert qualifying.strategy_id.nunique() == len(strategies) == len(index)

    eligible_scope = scope.eligible_1m.astype(str).str.lower().isin(["true", "1"])
    assert int(eligible_scope.sum()) == 331
    assert int((~eligible_scope).sum()) == 15
    assert scope.loc[~eligible_scope, "exclusion_reason"].fillna("").ne("").all()
    assert int(origin.loc[origin.source_origin.eq("WORKBOOK"), "strategies_audited"].iloc[0]) == 267
    assert int(origin.loc[origin.source_origin.eq("PRE_WORKBOOK"), "strategies_audited"].iloc[0]) == 64

    summary_pngs = set()
    detailed_pngs = set()
    for strategy in strategies.strategy_id.astype(str):
        folder = output / "strategies" / strategy
        summary = pd.read_csv(folder / "summary.csv")
        assert len(summary) == 27
        assert summary.symbol.nunique() == 9
        assert set(summary.timeframe) == {"1m", "10m", "15m"}
        assert summary.source_origin.nunique() == 1
        assert not any("5bp" in column.lower() for column in summary.columns)
        for timeframe in ["1m", "10m", "15m"]:
            path = folder / f"summary_{timeframe}.png"
            assert path.is_file() and path.stat().st_size > 0
            summary_pngs.add(path.resolve())
        for path in (folder / "performance").glob("*/*__performance.png"):
            detailed_pngs.add(path.resolve())

    expected_detail_paths = {
        (output / Path(value)).resolve()
        for value in qualifying.performance_figure_path.astype(str)
    }
    assert detailed_pngs == expected_detail_paths
    assert all(path.is_file() and path.stat().st_size > 0 for path in expected_detail_paths)
    assert len(summary_pngs) == len(strategies) * 3
    assert len(detailed_pngs) == len(qualifying)

    assert validation["status"] == "PASSED"
    assert validation["stage_status"] == "READY_FOR_USER_REVIEW"
    assert validation["stageB_started"] is False
    assert validation["tick_index_rebuild"] == 0
    assert validation["parameter_optimization"] == 0
    assert validation["strategy_semantic_changes"] == 0
    assert validation["five_bp_columns"] == 0
    assert "stageB_candidate" in preview.columns

    with ZipFile(args.zip_path) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert any(name.endswith("/strategy_index.csv") for name in names)
        assert any(name.endswith("/validation_summary.json") for name in names)

    result = {
        "status": "PASSED",
        "logical_cases": len(all_results),
        "qualifying_cases": len(qualifying),
        "qualifying_strategies": len(strategies),
        "summary_figures": len(summary_pngs),
        "detailed_figures": len(detailed_pngs),
        "nonqualifying_detailed_figures": 0,
        "stageB_started": False,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
