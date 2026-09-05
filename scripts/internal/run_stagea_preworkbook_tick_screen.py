#!/usr/bin/env python3
"""Run only missing PRE_WORKBOOK Stage-A cases with the frozen tick semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.build_boss_persistence_v2 import (  # noqa: E402
    directional_persistence_metrics,
)
from scripts.internal.run_boss_multitimeframe_tick_screen import (  # noqa: E402
    SYMBOLS,
    atomic_json,
    load_symbol,
    run_group_case,
)
from strategy_framework.registry import get_entry  # noqa: E402

TIMEFRAMES = ("1m", "10m", "15m")
PREWORKBOOK_REGISTRY = (
    ROOT
    / "outputs/internal_audit/execution_migration/registry"
    / "strategy_execution_migration_registry.csv"
)


def source_config_path(strategy_id: str) -> Path:
    entry = get_entry(strategy_id)
    path = ROOT / entry.default_config_path
    if not path.is_file():
        raise FileNotFoundError(f"default config missing for {strategy_id}: {path}")
    return path


def canonical_preworkbook_scope() -> pd.DataFrame:
    """Use the established, five-year-validated PRE_WORKBOOK inventory."""
    registry = pd.read_csv(PREWORKBOOK_REGISTRY, dtype=str).fillna("")
    if registry.strategy_name.duplicated().any():
        raise ValueError("duplicate PRE_WORKBOOK strategy identity")
    registry["included"] = registry.five_year_validated.str.lower().eq("yes")
    registry["source_origin"] = "PRE_WORKBOOK"
    registry["eligible_1m"] = registry.included
    registry["eligible_10m"] = registry.included
    registry["eligible_15m"] = registry.included
    registry["exclusion_reason"] = np.where(
        registry.included,
        "",
        "NOT_IN_CANONICAL_PRE_WORKBOOK_INVENTORY_FIVE_YEAR_VALIDATED_FALSE",
    )
    included = registry.loc[registry.included, "strategy_name"].tolist()
    if len(included) != 64:
        raise ValueError(f"expected canonical PRE_WORKBOOK inventory of 64, got {len(included)}")
    for strategy_id in included:
        source_config_path(strategy_id)
    return registry


def semantic_identity(strategy_id: str) -> tuple[str, dict[str, Any]]:
    """Keep source-defined long/short identities physically distinct."""
    path = source_config_path(strategy_id)
    source = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    payload = {
        "source_origin": "PRE_WORKBOOK",
        "strategy_id": strategy_id,
        "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, source


def run_symbol(args: argparse.Namespace) -> int:
    canonical_root = args.canonical_tick_root.resolve()
    window = json.loads(
        (canonical_root / "boss_tick_index_data_window.json").read_text(encoding="utf-8")
    )
    start = args.start or window["common_start"]
    end_exclusive = args.end_exclusive or window["common_end_exclusive"]
    end_inclusive = (date.fromisoformat(end_exclusive) - timedelta(days=1)).isoformat()
    end_ns = int(pd.Timestamp(end_exclusive, tz="UTC").value)
    bars, funding, execution, tick_prices, waits = load_symbol(
        args.market_root,
        canonical_root / "tick_execution_index",
        args.symbol,
        start,
        end_inclusive,
    )
    scope = canonical_preworkbook_scope()
    strategies = sorted(scope.loc[scope.included, "strategy_name"].tolist())
    if args.strategy:
        requested = set(args.strategy)
        unknown = requested - set(strategies)
        if unknown:
            raise ValueError(f"unknown/noncanonical PRE_WORKBOOK strategies: {sorted(unknown)}")
        strategies = [value for value in strategies if value in requested]

    completed = failures = physical = reused = 0
    progress = args.output_root / f"progress_{args.symbol}.json"
    for strategy_id in strategies:
        semantic_hash, source = semantic_identity(strategy_id)
        for timeframe in TIMEFRAMES:
            case_root = (
                args.output_root
                / "matrix_cases"
                / f"symbol={args.symbol}"
                / f"timeframe={timeframe}"
                / f"strategy={strategy_id}"
            )
            result_path = case_root / "summary.json"
            review_path = case_root / "review_timeseries.parquet"
            if result_path.is_file() and review_path.is_file():
                saved = json.loads(result_path.read_text(encoding="utf-8"))
                if saved.get("status") == "COMPLETED":
                    completed += 1
                    reused += 1
                    continue
            try:
                summary, review = run_group_case(
                    representative=strategy_id,
                    members=[strategy_id],
                    source=source,
                    semantic_hash=semantic_hash,
                    symbol=args.symbol,
                    timeframe=timeframe,
                    bars=bars,
                    funding=funding,
                    execution=execution,
                    tick_prices=tick_prices,
                    waits=waits,
                    end_ns=end_ns,
                )
                persistence = directional_persistence_metrics(review)
                summary.update(
                    {
                        "source_origin": "PRE_WORKBOOK",
                        "source_strategy_id": strategy_id,
                        "semantic_group_id": f"PRE_WORKBOOK:{strategy_id}",
                        **persistence,
                    }
                )
                atomic_json(result_path, summary)
                case_root.mkdir(parents=True, exist_ok=True)
                temporary = review_path.with_suffix(review_path.suffix + ".tmp")
                review.to_parquet(temporary, index=False, compression="zstd")
                os.replace(temporary, review_path)
                completed += 1
                physical += 1
            except Exception as exc:
                failures += 1
                atomic_json(
                    result_path,
                    {
                        "status": "FAILED",
                        "strategy_id": strategy_id,
                        "source_origin": "PRE_WORKBOOK",
                        "symbol": args.symbol,
                        "timeframe": timeframe,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            atomic_json(
                progress,
                {
                    "status": "RUNNING",
                    "symbol": args.symbol,
                    "logical_planned": len(strategies) * len(TIMEFRAMES),
                    "logical_completed": completed,
                    "logical_failures": failures,
                    "physical_runs_this_process": physical,
                    "reused_cases": reused,
                    "current_strategy": strategy_id,
                    "current_timeframe": timeframe,
                },
            )
    atomic_json(
        progress,
        {
            "status": "PASSED" if failures == 0 else "COMPLETED_WITH_FAILURES",
            "symbol": args.symbol,
            "logical_planned": len(strategies) * len(TIMEFRAMES),
            "logical_completed": completed,
            "logical_failures": failures,
            "physical_runs_this_process": physical,
            "reused_cases": reused,
        },
    )
    return 0 if failures == 0 else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=SYMBOLS)
    parser.add_argument("--strategy", action="append")
    parser.add_argument("--start")
    parser.add_argument("--end-exclusive")
    parser.add_argument(
        "--market-root",
        type=Path,
        default=ROOT / "historical_data/market_data",
    )
    parser.add_argument(
        "--canonical-tick-root",
        type=Path,
        default=ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs/baseline_evaluation/tick_review_stageA_9symbols_preworkbook",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_symbol(parse_args()))
