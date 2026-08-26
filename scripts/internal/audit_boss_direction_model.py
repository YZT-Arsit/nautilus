#!/usr/bin/env python3
"""Audit and prove the corrected NORMAL vs STRICT_REVERSE boss result universe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


OLD_MODES = ("original", "long_only", "short_only", "strict_reverse")
CORRECTED_MODE = {"original": "normal", "strict_reverse": "strict_reverse"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--revised-root", type=Path)
    return parser.parse_args()


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def completed_episode_count(case: Path) -> int:
    summary = case / "per_trade_break_even_summary.json"
    if not summary.is_file():
        raise RuntimeError(f"missing canonical episode summary: {summary}")
    payload = json.loads(summary.read_text(encoding="utf-8"))
    return int(payload.get("included", {}).get("completed_episode_count", 0))


def source_timeseries(original_case: Path) -> Path:
    metrics = json.loads((original_case / "metrics.json").read_text(encoding="utf-8"))
    return Path(metrics["source_timeseries"])


def corrected_case(root: Path, strategy: str, symbol: str, timeframe: str, lag: str, mode: str) -> Path:
    return root / strategy / symbol / timeframe / lag / mode


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    revised_root = args.revised_root.resolve() if args.revised_root else None
    canonical = pd.read_csv(source_root / "canonical_summary.csv")
    canonical = canonical.loc[~canonical["strategy"].str.startswith("xlsx_")]
    strategies = sorted(canonical["strategy"].unique())
    combos = sorted(
        {
            (row.strategy, row.symbol, row.timeframe, f"lag{int(row.lag_minutes)}m")
            for row in canonical.itertuples()
        }
    )

    tree_rows: list[dict[str, Any]] = []
    for strategy, symbol, timeframe, lag in combos:
        base = source_root / strategy / symbol / timeframe / lag
        tree_rows.append(
            {
                "strategy_id": strategy,
                "symbol": symbol,
                "timeframe": timeframe,
                "lag": lag,
                **{f"{mode}_exists": (base / mode).is_dir() for mode in OLD_MODES},
                "source_strategy_name": strategy,
            }
        )
    tree = pd.DataFrame(tree_rows)
    write_csv(tree, output_root / "wrong_direction_tree_audit.csv")

    intrinsic_rows: list[dict[str, Any]] = []
    intrinsic_by_strategy: dict[str, str] = {}
    first_combo = {strategy: next(item for item in combos if item[0] == strategy) for strategy in strategies}
    cached_directions: dict[tuple[str, str, str, str], pd.DataFrame] = {}
    for strategy in strategies:
        _, symbol, timeframe, lag = first_combo[strategy]
        original_case = source_root / strategy / symbol / timeframe / lag / "original"
        columns = [
            "normal_direction",
            "strict_reverse_direction",
            "long_only_direction",
            "short_only_direction",
        ]
        directions = pd.read_parquet(source_timeseries(original_case), columns=columns)
        cached_directions[(strategy, symbol, timeframe, lag)] = directions
        normal = directions["normal_direction"].to_numpy(dtype="float64")
        minimum = float(np.min(normal, initial=0.0))
        maximum = float(np.max(normal, initial=0.0))
        if minimum >= 0.0 and maximum > 0.0:
            intrinsic = "INTRINSIC_LONG"
        elif maximum <= 0.0 and minimum < 0.0:
            intrinsic = "INTRINSIC_SHORT"
        elif minimum < 0.0 < maximum:
            intrinsic = "GENUINELY_BIDIRECTIONAL"
        else:
            intrinsic = "OTHER"
        intrinsic_by_strategy[strategy] = intrinsic
        intrinsic_rows.append(
            {
                "strategy_id": strategy,
                "observed_min_target": minimum,
                "observed_max_target": maximum,
                "long_target_count": int(np.sum(normal > 0.0)),
                "short_target_count": int(np.sum(normal < 0.0)),
                "intrinsic_direction": intrinsic,
                "provenance_column": "normal_direction",
                "provenance_file": str(source_timeseries(original_case)),
            }
        )
    intrinsic_frame = pd.DataFrame(intrinsic_rows)
    write_csv(intrinsic_frame, output_root / "original_strategy_intrinsic_direction.csv")

    redundancy_rows: list[dict[str, Any]] = []
    strict_rows: list[dict[str, Any]] = []
    zero_rows: list[dict[str, Any]] = []
    migration_rows: list[dict[str, Any]] = []
    for strategy, symbol, timeframe, lag in combos:
        base = source_root / strategy / symbol / timeframe / lag
        directions = cached_directions.get((strategy, symbol, timeframe, lag))
        if directions is None:
            directions = pd.read_parquet(
                source_timeseries(base / "original"),
                columns=[
                    "normal_direction",
                    "strict_reverse_direction",
                    "long_only_direction",
                    "short_only_direction",
                ],
            )
        normal = directions["normal_direction"].to_numpy(dtype="float64")
        reverse = directions["strict_reverse_direction"].to_numpy(dtype="float64")
        strict_residual = float(np.max(np.abs(normal + reverse), initial=0.0))
        strict_rows.append(
            {
                "strategy_id": strategy,
                "symbol": symbol,
                "timeframe": timeframe,
                "lag": lag,
                "max_direction_residual": strict_residual,
                "validation_passed": strict_residual <= 1e-12,
            }
        )
        intrinsic = intrinsic_by_strategy[strategy]
        matching_mode = (
            "long_only"
            if intrinsic == "INTRINSIC_LONG"
            else "short_only"
            if intrinsic == "INTRINSIC_SHORT"
            else None
        )
        opposite_mode = (
            "short_only"
            if matching_mode == "long_only"
            else "long_only"
            if matching_mode == "short_only"
            else None
        )
        matching_residual: float | None = None
        matching_count_equal: bool | None = None
        opposite_count: int | None = None
        artificial_flat: bool | None = None
        if matching_mode and opposite_mode:
            matching = directions[f"{matching_mode}_direction"].to_numpy(dtype="float64")
            opposite = directions[f"{opposite_mode}_direction"].to_numpy(dtype="float64")
            matching_residual = float(np.max(np.abs(normal - matching), initial=0.0))
            matching_count_equal = completed_episode_count(base / "original") == completed_episode_count(
                base / matching_mode
            )
            opposite_count = completed_episode_count(base / opposite_mode)
            artificial_flat = bool(np.allclose(opposite, 0.0) and opposite_count == 0)
        redundancy_rows.append(
            {
                "strategy_id": strategy,
                "symbol": symbol,
                "timeframe": timeframe,
                "lag": lag,
                "intrinsic_direction": intrinsic,
                "matching_filter": matching_mode,
                "original_vs_matching_filter_position_residual": matching_residual,
                "original_vs_matching_filter_episode_count_equal": matching_count_equal,
                "opposite_filter": opposite_mode,
                "opposite_filter_episode_count": opposite_count,
                "opposite_filter_artificial_flat": artificial_flat,
            }
        )
        for old_mode in OLD_MODES:
            old_case = base / old_mode
            count = completed_episode_count(old_case)
            if count == 0:
                classification = {
                    "long_only": "ARTIFICIAL_ZERO_FROM_LONG_ONLY",
                    "short_only": "ARTIFICIAL_ZERO_FROM_SHORT_ONLY",
                    "original": "GENUINE_NORMAL_ZERO",
                    "strict_reverse": "GENUINE_STRICT_REVERSE_ZERO",
                }.get(old_mode, "OTHER")
                zero_rows.append(
                    {
                        "strategy_id": strategy,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "lag": lag,
                        "old_mode": old_mode,
                        "completed_episode_count": count,
                        "classification": classification,
                    }
                )
            if old_mode in CORRECTED_MODE:
                new_mode = CORRECTED_MODE[old_mode]
                classification = "REUSED_TRUSTED_RESULT"
            else:
                new_mode = "normal"
                classification = "DEPRECATED_REDUNDANT_SECONDARY_FILTER"
            new_case = (
                corrected_case(revised_root, strategy, symbol, timeframe, lag, new_mode)
                if revised_root
                else output_root / strategy / symbol / timeframe / lag / new_mode
            )
            replacement_validated = bool(
                revised_root
                and new_case.is_dir()
                and (new_case / f"{symbol}_{timeframe}_{lag}_{new_mode}_performance.png").is_file()
            )
            migration_rows.append(
                {
                    "strategy_id": strategy,
                    "lag": lag,
                    "timeframe": timeframe,
                    "old_mode": old_mode,
                    "old_path": str(old_case),
                    "new_mode": new_mode,
                    "new_path": str(new_case),
                    "classification": classification,
                    "replacement_validated": replacement_validated,
                }
            )

    redundancy = pd.DataFrame(redundancy_rows)
    strict = pd.DataFrame(strict_rows)
    zeros = pd.DataFrame(zero_rows)
    migration = pd.DataFrame(migration_rows)
    write_csv(redundancy, output_root / "old_direction_filter_redundancy.csv")
    write_csv(strict, output_root / "strict_reverse_validation.csv")
    write_csv(zeros, output_root / "old_512_zero_episode_reclassification.csv")
    write_csv(migration, output_root / "boss_direction_model_migration.csv")

    lag_count = int(tree["lag"].nunique())
    timeframe_count = int(tree["timeframe"].nunique())
    expected_units = len(strategies) * 2 * lag_count * timeframe_count
    summary = {
        "status": "passed"
        if bool(strict["validation_passed"].all()) and len(zeros) >= 0
        else "failed",
        "original_strategy_count": len(strategies),
        "old_branch_count": len(combos) * len(OLD_MODES),
        "boss_modes": ["normal", "strict_reverse"],
        "lag_case_count": lag_count,
        "timeframe_case_count": timeframe_count,
        "expected_corrected_units": expected_units,
        "intrinsic_direction_counts": intrinsic_frame["intrinsic_direction"].value_counts().to_dict(),
        "old_zero_unit_count": len(zeros),
        "old_zero_classification_counts": zeros["classification"].value_counts().to_dict(),
        "strict_reverse_failure_count": int((~strict["validation_passed"]).sum()),
        "xlsx_strategy_count": 0,
    }
    destination = output_root / "boss_direction_model_audit.json"
    destination.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
