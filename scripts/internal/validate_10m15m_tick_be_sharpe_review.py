#!/usr/bin/env python3
"""Independently validate the frozen 10m/15m tick BE/Sharpe delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
from PIL import Image, ImageStat


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_true(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def case_path(root: Path, semantic: str, symbol: str, timeframe: str) -> Path:
    return root / "matrix_cases" / f"symbol={symbol}" / f"timeframe={timeframe}" / f"semantic={semantic}" / "review_timeseries.parquet"


def independent_daily_sharpe(frame: pd.DataFrame) -> float:
    timestamps = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    cumulative = frame["cumulative_return_with_premium"].to_numpy(float)
    midnight = (timestamps.dt.hour == 0) & (timestamps.dt.minute == 0) & (timestamps.dt.second == 0)
    anchor_values = cumulative[midnight.to_numpy()]
    increments = np.diff(np.append(anchor_values, cumulative[-1]))
    increments = increments[np.isfinite(increments)]
    if len(increments) < 2:
        return float("nan")
    sample_sd = increments.std(ddof=1)
    if not np.isfinite(sample_sd) or sample_sd == 0:
        return float("nan")
    return float(increments.mean() / sample_sd * math.sqrt(365.0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=30)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    result_root = repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
    archive_path = output.with_suffix(".zip")

    all_results = pd.read_csv(output / "all_10m15m_results.csv")
    selected = pd.read_csv(output / "selected_strategies.csv")
    selected_cases = pd.read_csv(output / "selected_cases.csv")
    assert len(all_results) == 4806
    assert all_results.strategy_id.nunique() == 267
    assert set(all_results.timeframe) == {"10m", "15m"}
    assert all_results.symbol.nunique() == 9
    assert len(pd.read_csv(output / "be10_cases.csv")) == 403
    assert len(pd.read_csv(output / "good_sharpe_cases.csv")) == 353
    assert len(pd.read_csv(output / "strong_sharpe_cases.csv")) == 78
    assert len(selected) == 77
    assert int(is_true(selected.selected_by_BE).sum()) == 34
    assert int(is_true(selected.selected_by_Sharpe).sum()) == 64
    assert int((is_true(selected.selected_by_BE) & is_true(selected.selected_by_Sharpe)).sum()) == 22
    assert int((is_true(selected.selected_by_BE) & ~is_true(selected.selected_by_Sharpe)).sum()) == 12
    assert int((~is_true(selected.selected_by_BE) & is_true(selected.selected_by_Sharpe)).sum()) == 42
    assert int(is_true(selected.previous_selected).sum()) == 14
    assert len(selected_cases) == 516

    csv_paths = list(output.rglob("*.csv"))
    assert csv_paths
    for path in csv_paths:
        columns = pd.read_csv(path, nrows=0).columns
        assert not any("5bp" in column.lower() for column in columns), path

    for row in selected.itertuples(index=False):
        folder = output / Path(row.strategy_folder)
        summary = pd.read_csv(folder / "summary.csv")
        assert len(summary) == 18
        assert summary.semantic_group_id.nunique() == 1
        assert (folder / "summary_10m.png").is_file()
        assert (folder / "summary_15m.png").is_file()
    for relative in selected_cases.performance_figure_path:
        assert (output / Path(relative)).is_file(), relative

    png_paths = list(output.rglob("*.png"))
    assert len(png_paths) == 670
    for path in png_paths:
        assert path.stat().st_size > 10_000, path
        with Image.open(path) as image:
            image.verify()
        with Image.open(path).convert("RGB") as image:
            assert image.width >= 1000 and image.height >= 600, path
            extrema = ImageStat.Stat(image).extrema
            assert any(high > low for low, high in extrema), path

    physical = all_results.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"]).reset_index(drop=True)
    sample_positions = np.linspace(0, len(physical) - 1, min(args.sample_count, len(physical)), dtype=int)
    max_sharpe_residual = 0.0
    max_mdd_residual = 0.0
    for index in sample_positions:
        row = physical.iloc[index]
        frame = pd.read_parquet(case_path(result_root, row.semantic_execution_hash, row.symbol, row.timeframe))
        observed = independent_daily_sharpe(frame)
        expected = float(row.Sharpe)
        if np.isnan(observed) and np.isnan(expected):
            residual = 0.0
        else:
            residual = abs(observed - expected)
        max_sharpe_residual = max(max_sharpe_residual, residual)
        max_mdd_residual = max(max_mdd_residual, abs(float(frame.drawdown.min()) - float(row.Max_Drawdown)))
    assert max_sharpe_residual <= 1e-12
    assert max_mdd_residual <= 1e-12

    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        archive_members = len(archive.namelist())
    report = {
        "status": "PASSED",
        "logical_rows": len(all_results),
        "selected_independent_strategies": len(selected),
        "selected_detailed_figures": len(selected_cases),
        "png_count": len(png_paths),
        "summary_10m_count": len(list(output.glob("strategies/*/summary_10m.png"))),
        "summary_15m_count": len(list(output.glob("strategies/*/summary_15m.png"))),
        "sampled_sharpe_recomputations": len(sample_positions),
        "max_sampled_sharpe_residual": max_sharpe_residual,
        "max_sampled_mdd_residual": max_mdd_residual,
        "zip_test": "PASSED",
        "zip_members": archive_members,
        "zip_sha256": sha256(archive_path),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
