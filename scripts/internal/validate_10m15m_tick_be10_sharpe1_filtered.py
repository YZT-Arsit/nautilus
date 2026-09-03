#!/usr/bin/env python3
"""Validate the strict filtered 1m/10m/15m boss delivery."""

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


def case_path(root: Path, semantic: str, symbol: str, timeframe: str) -> Path:
    return root / "matrix_cases" / f"symbol={symbol}" / f"timeframe={timeframe}" / f"semantic={semantic}" / "review_timeseries.parquet"


def recompute_sharpe(frame: pd.DataFrame) -> float:
    timestamps = pd.to_datetime(frame.event_time_ns, unit="ns", utc=True)
    cumulative = frame.cumulative_return_with_premium.to_numpy(float)
    midnight = (timestamps.dt.hour == 0) & (timestamps.dt.minute == 0) & (timestamps.dt.second == 0)
    daily = np.diff(np.append(cumulative[midnight.to_numpy()], cumulative[-1]))
    daily = daily[np.isfinite(daily)]
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return float("nan")
    return float(daily.mean() / daily.std(ddof=1) * math.sqrt(365.0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    result_root = repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"

    cases = pd.read_csv(output / "qualifying_cases.csv")
    strategies = pd.read_csv(output / "qualifying_strategies.csv")
    index = pd.read_csv(output / "strategy_index.csv")
    assert len(cases) == 164
    assert len(strategies) == 61
    assert len(index) == 61
    assert set(cases.timeframe) <= {"10m", "15m"}
    assert cases.Signed_BE_bps.abs().gt(10).all()
    assert cases.Sharpe.abs().gt(1).all()
    assert int(cases.timeframe.eq("10m").sum()) == 76
    assert int(cases.timeframe.eq("15m").sum()) == 88
    assert len(pd.read_csv(output / "positive_quality_cases.csv")) == 94

    for csv_path in output.rglob("*.csv"):
        assert not any("5bp" in column.lower() for column in pd.read_csv(csv_path, nrows=0).columns), csv_path
    for row in strategies.itertuples(index=False):
        folder = output / Path(row.strategy_folder)
        summary = pd.read_csv(folder / "summary.csv")
        assert len(summary) == 27
        assert set(summary.timeframe) == {"1m", "10m", "15m"}
        assert summary.symbol.nunique() == 9
        for timeframe in ("1m", "10m", "15m"):
            assert (folder / f"summary_{timeframe}.png").is_file()
        assert not (folder / "performance" / "1m").exists()
    expected_paths = {str(Path(path)) for path in cases.performance_figure_path}
    actual_paths = {
        str(path.relative_to(output))
        for path in output.glob("strategies/*/performance/*/*.png")
    }
    assert actual_paths == expected_paths

    png_paths = list(output.rglob("*.png"))
    assert len(png_paths) == 347
    for path in png_paths:
        assert path.stat().st_size > 10_000
        with Image.open(path) as image:
            image.verify()
        with Image.open(path).convert("RGB") as image:
            assert image.width >= 1000 and image.height >= 600
            assert any(high > low for low, high in ImageStat.Stat(image).extrema)

    summaries = pd.concat([
        pd.read_csv(output / Path(folder) / "summary.csv")
        for folder in strategies.strategy_folder
    ], ignore_index=True)
    physical = summaries.drop_duplicates(["semantic_group_id", "symbol", "timeframe"]).reset_index(drop=True)
    sample_positions = np.linspace(0, len(physical) - 1, min(36, len(physical)), dtype=int)
    max_sharpe_residual = 0.0
    max_mdd_residual = 0.0
    for position in sample_positions:
        row = physical.iloc[position]
        frame = pd.read_parquet(case_path(result_root, row.semantic_group_id, row.symbol, row.timeframe))
        observed = recompute_sharpe(frame)
        expected = float(row.Sharpe)
        residual = 0.0 if np.isnan(observed) and np.isnan(expected) else abs(observed - expected)
        max_sharpe_residual = max(max_sharpe_residual, residual)
        max_mdd_residual = max(max_mdd_residual, abs(float(frame.drawdown.min()) - float(row.Max_Drawdown)))
    assert max_sharpe_residual <= 1e-12
    assert max_mdd_residual <= 1e-12

    archive_path = output.with_suffix(".zip")
    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        archive_members = len(archive.namelist())
    report = {
        "status": "PASSED",
        "qualifying_cases": len(cases),
        "qualifying_strategies": len(strategies),
        "summary_figures": 183,
        "detailed_figures": len(actual_paths),
        "nonqualifying_detailed_figures": 0,
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
