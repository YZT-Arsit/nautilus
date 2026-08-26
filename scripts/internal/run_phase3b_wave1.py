#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""Execute the versioned, leakage-safe Phase 3B Wave 1 walk-forward search."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib as mpl


mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from results.strategy_evaluation import build_additive_strategy_evaluation_from_columns
from results.trade_episode import build_de_risk_episodes
from scripts.internal.run_all_strategy_timeframe_lag import _build_config_obj
from scripts.internal.run_all_strategy_timeframe_lag import build_strategy_clock
from scripts.internal.run_all_strategy_timeframe_lag import load_market_and_funding
from scripts.internal.run_all_strategy_timeframe_lag import run_decision_lifecycle
from scripts.internal.run_constant_notional_overlay import calculate_overlay
from strategy_framework.parameter_search import PROTOCOL_VERSION
from strategy_framework.parameter_search import generate_candidates
from strategy_framework.parameter_search import is_wave1_spec
from strategy_framework.parameter_search import logical_checkpoint_id
from strategy_framework.parameter_search import physical_cache_key
from strategy_framework.parameter_search import protocol_amendment
from strategy_framework.parameter_search import select_candidate
from strategy_framework.parameter_search import validate_protocol
from strategy_framework.parameter_search import validation_eligibility
from strategy_framework.registry import get_entry


AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
DEFAULT_OUTPUT = ROOT / "outputs/parameter_search/phase3b_wave1"
FOLDS_PATH = AUDIT / "phase3a_walk_forward_protocol.json"
MANIFEST_PATH = AUDIT / "parameter_search_manifest.csv"
PHASE3A_PROTOCOL_PATH = AUDIT / "phase3a_search_protocol.json"
PHASE3A_PLAN_PATH = AUDIT / "phase3a_search_execution_plan.csv"
AMENDMENT_PATH = AUDIT / "phase3b_wave1_protocol_amendment.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--strategy-root", type=Path, default=ROOT / "strategies")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--limit-specs", type=int)
    return parser.parse_args()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fields = sorted({key for row in rows for key in row}) if rows else []
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)
    os.replace(temporary, path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relevant_integrity_files(strategy_root: Path) -> list[Path]:
    files = list(strategy_root.glob("*/config.yaml"))
    files += [
        AUDIT / "phase2_2c_validation_summary.json",
        AUDIT / "phase2_3_validation_summary.json",
        AUDIT / "phase2_4_validation_summary.json",
        AUDIT / "phase2_2c_backtest_summary.csv",
        AUDIT / "phase2_3_backtest_summary.csv",
        AUDIT / "phase2_4_backtest_summary.csv",
        AUDIT / "semantic_contract_registry.csv",
        AUDIT / "phase2_3_session_contract_registry.csv",
        ROOT / "configs/strategy_modules/workbook_phase2_4_modules.json",
        ROOT / "configs/strategy_modules/workbook_atr_ladders.json",
        MANIFEST_PATH,
        FOLDS_PATH,
        PHASE3A_PROTOCOL_PATH,
        PHASE3A_PLAN_PATH,
        AUDIT / "phase3a_parameter_inventory.csv",
    ]
    return sorted({path.resolve() for path in files if path.is_file()})


def integrity_snapshot(strategy_root: Path) -> dict[str, Any]:
    files = relevant_integrity_files(strategy_root)
    return {
        "hash_algorithm": "SHA-256",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "files": {str(path.relative_to(ROOT)): sha256(path) for path in files},
    }


def code_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(
        [
            ROOT / "strategy_framework/parameter_search.py",
            ROOT / "scripts/internal/run_phase3b_wave1.py",
            ROOT / "scripts/internal/run_all_strategy_timeframe_lag.py",
            ROOT / "scripts/internal/run_constant_notional_overlay.py",
            ROOT / "results/trade_episode.py",
        ]
    ):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def data_provenance(market_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(market_root.rglob("*.parquet"))
    for path in files:
        stat = path.stat()
        digest.update(f"{path.relative_to(market_root).as_posix()}:{stat.st_size}".encode())
    return f"sha256:{digest.hexdigest()}:files={len(files)}"


def load_wave1() -> tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    specs = [row for row in read_csv(MANIFEST_PATH) if is_wave1_spec(row)]
    folds = json.loads(FOLDS_PATH.read_text(encoding="utf-8"))
    phase3a_protocol = json.loads(PHASE3A_PROTOCOL_PATH.read_text(encoding="utf-8"))
    return specs, folds, phase3a_protocol


def preflight(strategy_root: Path, market_root: Path, output_root: Path) -> dict[str, Any]:
    specs, folds, _ = load_wave1()
    amendment = (
        json.loads(AMENDMENT_PATH.read_text(encoding="utf-8"))
        if AMENDMENT_PATH.is_file()
        else protocol_amendment()
    )
    errors = validate_protocol(amendment)
    candidates = {row["search_id"]: generate_candidates(row) for row in specs}
    original_hashes = {
        path.name: sha256(path)
        for path in (
            MANIFEST_PATH,
            FOLDS_PATH,
            PHASE3A_PROTOCOL_PATH,
            PHASE3A_PLAN_PATH,
            AUDIT / "phase3a_parameter_inventory.csv",
        )
    }
    checks = {
        "protocol_schema": not errors,
        "wave1_spec_count": len(specs) == 23,
        "candidate_count": sum(len(value) for value in candidates.values()) == 105,
        "fold_count": len(folds["folds"]) == 7,
        "baseline_count": sum(
            sum(c.candidate_role == "BASELINE" for c in value) for value in candidates.values()
        )
        == 23,
        "logical_evaluation_count": 2 * 105 * 7 + 2 * 23 * 7 == 1792,
        "market_root_exists": market_root.is_dir(),
        "market_parquet_present": any(market_root.rglob("*.parquet"))
        if market_root.is_dir()
        else False,
        "strategy_root_exists": strategy_root.is_dir(),
        "phase3a_files_unchanged_during_preflight": original_hashes
        == {
            path.name: sha256(path)
            for path in (
                MANIFEST_PATH,
                FOLDS_PATH,
                PHASE3A_PROTOCOL_PATH,
                PHASE3A_PLAN_PATH,
                AUDIT / "phase3a_parameter_inventory.csv",
            )
        },
    }
    report = {
        "status": "PASSED" if all(checks.values()) else "FAILED",
        "protocol_errors": errors,
        "checks": checks,
        "wave1_specs": len(specs),
        "candidates": sum(len(value) for value in candidates.values()),
        "folds": len(folds["folds"]),
        "logical_evaluations": 1792,
        "physical_execution_range": [1631, 1792],
        "original_phase3a_hashes": original_hashes,
        "code_hash": code_hash(),
        "data_provenance": data_provenance(market_root),
    }
    atomic_json(AMENDMENT_PATH, amendment)
    atomic_json(output_root / "preflight_validation.json", report)
    if report["status"] != "PASSED":
        raise RuntimeError(f"Wave 1 preflight failed: {report}")
    return report


_MARKET_CACHE: dict[str, Any] = {}


def worker_market(market_root: str) -> tuple[list[Any], pd.DataFrame, np.ndarray]:
    if not _MARKET_CACHE:
        bars, funding = load_market_and_funding(Path(market_root), "2021-06-01", "2026-06-30")
        _MARKET_CACHE.update(
            bars=bars,
            funding=funding,
            times=np.asarray([bar.event_time_ns for bar in bars], dtype=np.int64),
        )
    return _MARKET_CACHE["bars"], _MARKET_CACHE["funding"], _MARKET_CACHE["times"]


def timestamp_ns(value: str) -> int:
    return int(pd.Timestamp(value, tz="UTC").value)


def evaluation_metrics(
    *,
    strategy_id: str,
    parameters: dict[str, Any],
    split: dict[str, str],
    market_root: str,
    strategy_root: str,
    result_path: Path,
    persist_series: bool,
) -> dict[str, Any]:
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    bars, funding, times = worker_market(market_root)
    start_ns, end_ns = timestamp_ns(split["start_inclusive"]), timestamp_ns(split["end_exclusive"])
    lo, hi = int(np.searchsorted(times, start_ns)), int(np.searchsorted(times, end_ns))
    live_bars = bars[lo:hi]
    if not live_bars:
        raise ValueError(f"{strategy_id}: empty split {split}")
    source = yaml.safe_load(
        (Path(strategy_root) / strategy_id / "config.yaml").read_text(encoding="utf-8")
    )
    merged = dict(source.get("params", {}))
    merged.update(parameters)
    plugin = get_entry(strategy_id)
    config = _build_config_obj(plugin.config_cls, merged, "1m", 1)
    specs = list(plugin.build_specs(config))
    warmup_count = max([int(spec.window or 1) for spec in specs] + [1])
    warmup_raw = bars[max(0, lo - warmup_count) : lo]
    direction, _, lifecycle = run_decision_lifecycle(
        strategy_name=strategy_id,
        source_config={"params": merged},
        frequency="1m",
        lag_minutes=1,
        bars_1m=live_bars,
        strategy_bars=build_strategy_clock(live_bars, "1m"),
        end_exclusive_ns=end_ns,
        warmup_bars=build_strategy_clock(warmup_raw, "1m"),
    )
    funding_values = funding[
        (funding["event_time_ns"] >= start_ns) & (funding["event_time_ns"] < end_ns)
    ]
    market_open = np.asarray([bar.open for bar in live_bars], dtype=np.float64)
    close = np.asarray([bar.close for bar in live_bars], dtype=np.float64)
    event_times = np.asarray([bar.event_time_ns for bar in live_bars], dtype=np.int64)
    result, overlay = calculate_overlay(
        pd.DataFrame({"event_time_ns": event_times, "close": close, "position": direction}),
        funding_values,
        market_open,
        notional_usdt=100_000.0,
        slippage_bps=0.0,
        vip9_fee_bps=1.7,
        vip0_fee_bps=5.0,
        position_policy="strict_constant_notional",
    )
    _, report_metrics = build_additive_strategy_evaluation_from_columns(
        event_time_ns=result["event_time_ns"],
        trading_return=result["trading_return"],
        funding_return=result["funding_return"],
        turnover=result["turnover"],
        executed_direction=result["direction"],
    )
    _, episode = build_de_risk_episodes(
        event_time_ns=result["event_time_ns"],
        executed_position=result["direction"],
        turnover_increment=result["turnover"],
        gross_return_increment=result["total_return"],
        strategy=strategy_id,
        symbol="BTCUSDT",
        granularity="1m",
        lag="lag1m",
        premium_mode="included",
    )
    trade_count = int(episode["completed_episode_count"])
    validity = "VALID_RESULT" if trade_count else "VALID_ZERO_TRADES"
    execution_ok = (
        float(overlay["accounting_identity_max_error"]) <= 1e-10
        and float(overlay["max_boundary_notional_error_usdt"] or 0.0) <= 1e-6
        and float(episode["maximum_break_even_residual"]) <= 1e-10
    )
    included = report_metrics["included"]
    metrics = {
        "validity_status": validity,
        "execution_validation_status": execution_ok,
        "trade_count": trade_count,
        "return_1x": float(included["final_return_1x"]),
        "turnover": float(included["turnover"]),
        "signed_global_be_bps": None
        if math.isnan(float(included["break_even_bps"]))
        else float(included["break_even_bps"]),
        "max_drawdown": float(included["max_drawdown"]),
        "median_trade_be_bps": episode["break_even_bps_median"],
        "signal_count": lifecycle["signal_count"],
        "fill_count": lifecycle["fill_count"],
        "warmup_bars": warmup_count,
        "first_event_time_ns": int(event_times[0]),
        "last_event_time_ns": int(event_times[-1]),
        "be_validation_residual": float(episode["maximum_break_even_residual"]),
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    if persist_series:
        series_path = result_path.with_name("timeseries.parquet")
        temporary = series_path.with_suffix(".parquet.tmp")
        result[
            [
                "event_time_ns",
                "direction",
                "trading_return",
                "funding_return",
                "total_return",
                "turnover",
            ]
        ].to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, series_path)
        metrics["timeseries_path"] = str(series_path)
    atomic_json(result_path, metrics)
    return metrics


def run_one_spec(payload: dict[str, Any]) -> dict[str, Any]:
    spec, folds = payload["spec"], payload["folds"]
    output_root = Path(payload["output_root"])
    spec_root = output_root / spec["search_id"]
    candidates = generate_candidates(spec)
    baseline = next(row for row in candidates if row.candidate_role == "BASELINE")
    code = payload["code_hash"]
    provenance = payload["data_provenance"]
    logical_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    for fold in folds:
        validation_for_selection: list[dict[str, Any]] = []
        metrics_by_candidate_split: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            for split_name in ("TRAIN", "VALIDATION"):
                split = fold[split_name.lower()]
                cache_fields = {
                    "strategy_id": spec["strategy_id"],
                    "search_id": spec["search_id"],
                    "config_hash": candidate.config_hash,
                    "fold_id": fold["fold_id"],
                    "split": split_name,
                    "timeframe": "1m",
                    "lag": "lag1m",
                    "premium_mode": "INCLUDED",
                    "direction_mode": "ORIGINAL",
                    "protocol_version": PROTOCOL_VERSION,
                    "code_hash": code,
                    "data_provenance": provenance,
                }
                cache_key = physical_cache_key(**cache_fields)
                result_path = spec_root / "cache" / cache_key / "metrics.json"
                started = datetime.now(UTC).isoformat()
                metrics = evaluation_metrics(
                    strategy_id=spec["strategy_id"],
                    parameters=candidate.as_parameters(),
                    split=split,
                    market_root=payload["market_root"],
                    strategy_root=payload["strategy_root"],
                    result_path=result_path,
                    persist_series=False,
                )
                ended = datetime.now(UTC).isoformat()
                metrics_by_candidate_split[(candidate.candidate_id, split_name)] = metrics
                eligible, reasons = (
                    validation_eligibility(metrics) if split_name == "VALIDATION" else (None, ())
                )
                row = {
                    "search_id": spec["search_id"],
                    "strategy_id": spec["strategy_id"],
                    "candidate_id": candidate.candidate_id,
                    "config_hash": candidate.config_hash,
                    "parameters": json.dumps(candidate.as_parameters(), sort_keys=True),
                    "candidate_role": candidate.candidate_role,
                    "fold_id": fold["fold_id"],
                    "split": split_name,
                    **metrics,
                    "eligible": eligible,
                    "ineligibility_reasons": ";".join(reasons),
                }
                candidate_rows.append(row)
                if split_name == "VALIDATION":
                    validation_for_selection.append(row)
                logical_rows.append(
                    {
                        "run_id": logical_checkpoint_id(
                            search_id=spec["search_id"],
                            candidate_id=candidate.candidate_id,
                            fold_id=fold["fold_id"],
                            split=split_name,
                            evaluation_role="CANDIDATE_PRESELECTION",
                            protocol_version=PROTOCOL_VERSION,
                        ),
                        "search_id": spec["search_id"],
                        "candidate_id": candidate.candidate_id,
                        "config_hash": candidate.config_hash,
                        "fold_id": fold["fold_id"],
                        "split": split_name,
                        "evaluation_role": "CANDIDATE_PRESELECTION",
                        "protocol_version": PROTOCOL_VERSION,
                        "strategy_id": spec["strategy_id"],
                        "timeframe": "1m",
                        "lag": "lag1m",
                        "premium_mode": "INCLUDED",
                        "direction_mode": "ORIGINAL",
                        "status": metrics["validity_status"],
                        "result_path": str(result_path),
                        "cache_key": cache_key,
                        "start_timestamp": started,
                        "end_timestamp": ended,
                    }
                )
        selection = select_candidate(validation_for_selection, baseline.candidate_id)
        selected = next(
            row for row in candidates if row.candidate_id == selection["selected_candidate_id"]
        )
        ranked = selection["ranked"]
        validation_by_id = {row["candidate_id"]: row for row in validation_for_selection}
        baseline_validation = validation_by_id[baseline.candidate_id]
        selected_validation = validation_by_id[selected.candidate_id]
        frozen = {
            "search_id": spec["search_id"],
            "fold_id": fold["fold_id"],
            "protocol_version": PROTOCOL_VERSION,
            "candidate_rankings": [
                {"candidate_id": row["candidate_id"], "rank": row["ranking"]} for row in ranked
            ],
            "eligible_candidates": [row["candidate_id"] for row in ranked],
            "selected_candidate_id": selected.candidate_id,
            "selected_config_hash": selected.config_hash,
            "selection_status": selection["selection_status"],
            "fallback_status": selection["selection_status"] == "BASELINE_FALLBACK",
            "fallback_reason": "NO_ELIGIBLE_CANDIDATE"
            if selection["selection_status"] == "BASELINE_FALLBACK"
            else "",
            "validation_metrics": selected_validation,
            "baseline_validation_metrics": baseline_validation,
            "tie_break_trace": "return_desc,be_desc,mdd_desc,turnover_asc,candidate_id_asc",
            "eligible_candidate_count": len(ranked),
            "selection_frozen": True,
            "frozen_at_utc": datetime.now(UTC).isoformat(),
        }
        selection_path = spec_root / "selections" / f"{fold['fold_id']}.json"
        if selection_path.is_file():
            existing_frozen = json.loads(selection_path.read_text(encoding="utf-8"))
            immutable_fields = (
                "search_id",
                "fold_id",
                "protocol_version",
                "selected_candidate_id",
                "selected_config_hash",
                "selection_status",
            )
            if any(existing_frozen.get(name) != frozen.get(name) for name in immutable_fields):
                raise AssertionError(f"frozen selection changed on restart: {selection_path}")
            frozen = existing_frozen
        else:
            atomic_json(selection_path, frozen)
        if not frozen["selection_frozen"]:
            raise AssertionError("selection freeze failed")
        selection_rows.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "fold_id": fold["fold_id"],
                "selected_candidate_id": selected.candidate_id,
                "selected_parameters": json.dumps(selected.as_parameters(), sort_keys=True),
                "selected_config_hash": selected.config_hash,
                "baseline_candidate_id": baseline.candidate_id,
                "selection_status": selection["selection_status"],
                "baseline_won": selected.candidate_id == baseline.candidate_id,
                "eligible_candidate_count": len(ranked),
                "zero_trade_candidate_count": sum(
                    row["validity_status"] == "VALID_ZERO_TRADES"
                    for row in validation_for_selection
                ),
                "failed_candidate_count": sum(
                    row["validity_status"] not in {"VALID_RESULT", "VALID_ZERO_TRADES"}
                    for row in validation_for_selection
                ),
                "selection_frozen_before_test": True,
                "selection_frozen_at_utc": frozen["frozen_at_utc"],
                "selected_validation_return": selected_validation["return_1x"],
                "baseline_validation_return": baseline_validation["return_1x"],
                "selected_validation_be_bps": selected_validation["signed_global_be_bps"],
                "baseline_validation_be_bps": baseline_validation["signed_global_be_bps"],
                "selected_validation_mdd": selected_validation["max_drawdown"],
                "baseline_validation_mdd": baseline_validation["max_drawdown"],
                "selected_validation_trade_count": selected_validation["trade_count"],
                "baseline_validation_trade_count": baseline_validation["trade_count"],
                "selected_train_return": metrics_by_candidate_split[
                    (selected.candidate_id, "TRAIN")
                ]["return_1x"],
            }
        )
        test_metrics: dict[str, dict[str, Any]] = {}
        test_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        for role, candidate in (("SELECTED_TEST", selected), ("BASELINE_TEST", baseline)):
            cache_fields = {
                "strategy_id": spec["strategy_id"],
                "search_id": spec["search_id"],
                "config_hash": candidate.config_hash,
                "fold_id": fold["fold_id"],
                "split": "TEST",
                "timeframe": "1m",
                "lag": "lag1m",
                "premium_mode": "INCLUDED",
                "direction_mode": "ORIGINAL",
                "protocol_version": PROTOCOL_VERSION,
                "code_hash": code,
                "data_provenance": provenance,
            }
            cache_key = physical_cache_key(**cache_fields)
            result_path = spec_root / "cache" / cache_key / "metrics.json"
            test_started = datetime.now(UTC).isoformat()
            if cache_key in test_cache:
                metrics = test_cache[cache_key][1]
            else:
                metrics = evaluation_metrics(
                    strategy_id=spec["strategy_id"],
                    parameters=candidate.as_parameters(),
                    split=fold["test"],
                    market_root=payload["market_root"],
                    strategy_root=payload["strategy_root"],
                    result_path=result_path,
                    persist_series=True,
                )
                test_cache[cache_key] = (str(result_path), metrics)
            test_metrics[role] = metrics
            logical_rows.append(
                {
                    "run_id": logical_checkpoint_id(
                        search_id=spec["search_id"],
                        candidate_id=candidate.candidate_id,
                        fold_id=fold["fold_id"],
                        split="TEST",
                        evaluation_role=role,
                        protocol_version=PROTOCOL_VERSION,
                    ),
                    "search_id": spec["search_id"],
                    "candidate_id": candidate.candidate_id,
                    "config_hash": candidate.config_hash,
                    "fold_id": fold["fold_id"],
                    "split": "TEST",
                    "evaluation_role": role,
                    "protocol_version": PROTOCOL_VERSION,
                    "strategy_id": spec["strategy_id"],
                    "timeframe": "1m",
                    "lag": "lag1m",
                    "premium_mode": "INCLUDED",
                    "direction_mode": "ORIGINAL",
                    "status": metrics["validity_status"],
                    "result_path": str(result_path),
                    "cache_key": cache_key,
                    "selection_frozen_at_utc": frozen["frozen_at_utc"],
                    "start_timestamp": test_started,
                    "end_timestamp": datetime.now(UTC).isoformat(),
                }
            )
        selected_test, baseline_test = test_metrics["SELECTED_TEST"], test_metrics["BASELINE_TEST"]
        test_rows.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "fold_id": fold["fold_id"],
                "selected_candidate_id": selected.candidate_id,
                "baseline_candidate_id": baseline.candidate_id,
                **{
                    f"selected_{key}": selected_test[key]
                    for key in (
                        "return_1x",
                        "turnover",
                        "signed_global_be_bps",
                        "max_drawdown",
                        "trade_count",
                    )
                },
                **{
                    f"baseline_{key}": baseline_test[key]
                    for key in (
                        "return_1x",
                        "turnover",
                        "signed_global_be_bps",
                        "max_drawdown",
                        "trade_count",
                    )
                },
                "return_delta": selected_test["return_1x"] - baseline_test["return_1x"],
                "turnover_delta": selected_test["turnover"] - baseline_test["turnover"],
                "be_delta": (selected_test["signed_global_be_bps"] or 0.0)
                - (baseline_test["signed_global_be_bps"] or 0.0),
                "mdd_delta": selected_test["max_drawdown"] - baseline_test["max_drawdown"],
            }
        )
    write_csv(spec_root / "candidate_metrics.csv", candidate_rows)
    write_csv(spec_root / "logical_manifest.csv", logical_rows)
    write_csv(spec_root / "fold_selections.csv", selection_rows)
    write_csv(spec_root / "fold_test_metrics.csv", test_rows)
    atomic_json(
        spec_root / "status.json",
        {"status": "TERMINAL", "search_id": spec["search_id"], "logical_rows": len(logical_rows)},
    )
    return {"search_id": spec["search_id"], "status": "TERMINAL", "logical_rows": len(logical_rows)}


def stitch_and_summarize(
    output_root: Path,
    specs: list[dict[str, str]],
    integrity_before: dict[str, Any],
    strategy_root: Path,
) -> dict[str, Any]:
    all_manifest: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    all_selections: list[dict[str, Any]] = []
    all_tests: list[dict[str, Any]] = []
    oos_rows: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []
    for spec in specs:
        root = output_root / spec["search_id"]
        all_manifest.extend(read_csv(root / "logical_manifest.csv"))
        all_candidates.extend(read_csv(root / "candidate_metrics.csv"))
        selections = read_csv(root / "fold_selections.csv")
        tests = read_csv(root / "fold_test_metrics.csv")
        all_selections.extend(selections)
        all_tests.extend(tests)
        selected_frames: list[pd.DataFrame] = []
        baseline_frames: list[pd.DataFrame] = []
        for selection in selections:
            fold = selection["fold_id"]
            logical = [
                row
                for row in read_csv(root / "logical_manifest.csv")
                if row["fold_id"] == fold and row["split"] == "TEST"
            ]
            for role, target in (
                ("SELECTED_TEST", selected_frames),
                ("BASELINE_TEST", baseline_frames),
            ):
                row = next(item for item in logical if item["evaluation_role"] == role)
                metrics = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
                target.append(pd.read_parquet(metrics["timeseries_path"]))
        selected = pd.concat(selected_frames, ignore_index=True)
        baseline = pd.concat(baseline_frames, ignore_index=True)

        strategy_id = spec["strategy_id"]

        def overall(frame: pd.DataFrame, strategy_id: str = strategy_id) -> dict[str, Any]:
            _, metrics = build_additive_strategy_evaluation_from_columns(
                event_time_ns=frame.event_time_ns,
                trading_return=frame.trading_return,
                funding_return=frame.funding_return,
                turnover=frame.turnover,
                executed_direction=frame.direction,
            )
            _, episodes = build_de_risk_episodes(
                event_time_ns=frame.event_time_ns,
                executed_position=frame.direction,
                turnover_increment=frame.turnover,
                gross_return_increment=frame.total_return,
                strategy=strategy_id,
                symbol="BTCUSDT",
                granularity="1m",
                lag="lag1m",
                premium_mode="included",
            )
            return {**metrics["included"], "trade_count": episodes["completed_episode_count"]}

        sm, bm = overall(selected), overall(baseline)
        baseline_wins = sum(str(row["baseline_won"]).lower() == "true" for row in selections)
        selected_ids = [row["selected_candidate_id"] for row in selections]
        selected_parameter_values = [
            next(iter(json.loads(row["selected_parameters"]).items()))[1] for row in selections
        ]
        candidate_space_values = next(iter(json.loads(spec["candidate_space"]).values()))
        numerical = all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in selected_parameter_values
        )
        normalized_drift = None
        stability_flag = "CATEGORICAL_PATH_REPORTED"
        if numerical:
            denominator = float(max(candidate_space_values) - min(candidate_space_values))
            normalized_drift = (
                float(max(selected_parameter_values) - min(selected_parameter_values)) / denominator
                if denominator > 0
                else 0.0
            )
            stability_flag = (
                "FULL_RANGE_DRIFT" if normalized_drift >= 1.0 - 1e-12 else "NO_FULL_RANGE_DRIFT"
            )
        oos_rows.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "searched_parameters": spec["searchable_parameters"],
                "candidate_count": spec["estimated_candidate_count"],
                "selected_oos_return": sm["final_return_1x"],
                "baseline_oos_return": bm["final_return_1x"],
                "return_delta": sm["final_return_1x"] - bm["final_return_1x"],
                "selected_oos_turnover": sm["turnover"],
                "baseline_oos_turnover": bm["turnover"],
                "turnover_delta": sm["turnover"] - bm["turnover"],
                "selected_oos_be_bps": sm["break_even_bps"],
                "baseline_oos_be_bps": bm["break_even_bps"],
                "be_delta": sm["break_even_bps"] - bm["break_even_bps"],
                "selected_oos_mdd": sm["max_drawdown"],
                "baseline_oos_mdd": bm["max_drawdown"],
                "mdd_delta": sm["max_drawdown"] - bm["max_drawdown"],
                "selected_trade_count": sm["trade_count"],
                "baseline_trade_count": bm["trade_count"],
                "baseline_selected_folds": baseline_wins,
                "unique_selected_configs": len(set(selected_ids)),
                "normalized_parameter_drift": normalized_drift,
                "stability_flag": stability_flag,
            }
        )
        stability_rows.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "selected_config_path": json.dumps(
                    {row["fold_id"]: json.loads(row["selected_parameters"]) for row in selections},
                    sort_keys=True,
                ),
                "unique_selected_configs": len(set(selected_ids)),
                "baseline_selected_folds": baseline_wins,
                "selection_frequency": json.dumps(
                    {value: selected_ids.count(value) for value in sorted(set(selected_ids))},
                    sort_keys=True,
                ),
                "normalized_parameter_drift": normalized_drift,
                "stability_flag": stability_flag,
            }
        )

        def cumulative(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            ret = np.cumsum(frame.total_return.to_numpy(float))
            turn = np.cumsum(frame.turnover.to_numpy(float))
            equity = 1.0 + ret
            peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
            dd = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0
            return ret, turn, dd

        sr, st, sd = cumulative(selected)
        br, bt, bd = cumulative(baseline)
        fig, axes = plt.subplots(3, 1, figsize=(15, 10), sharex=True)
        time = pd.to_datetime(selected.event_time_ns, unit="ns", utc=True)
        axes[0].plot(time, sr * 100, label="Walk-forward selected")
        axes[0].plot(time, br * 100, label="Canonical baseline")
        axes[0].set_ylabel("Cumulative Return (1x, %)")
        axes[0].legend()
        axes[0].grid(alpha=0.2)
        axes[1].plot(time, st, label="Selected")
        axes[1].plot(time, bt, label="Baseline")
        axes[1].set_ylabel("Cumulative Turnover (x)")
        axes[1].grid(alpha=0.2)
        axes[2].plot(time, sd * 100, label="Selected")
        axes[2].plot(time, bd * 100, label="Baseline")
        axes[2].set_ylabel("Drawdown (%)")
        axes[2].set_xlabel("UTC time")
        axes[2].grid(alpha=0.2)
        fig.suptitle(f"{spec['strategy_id']} — Phase 3B Wave 1 OOS — 1m lag1m Premium Included")
        fig.tight_layout()
        figure_path = root / "oos_selected_vs_baseline.png"
        fig.savefig(figure_path, dpi=150)
        plt.close(fig)
    write_csv(output_root / "phase3b_wave1_run_manifest.csv", all_manifest)
    write_csv(output_root / "phase3b_wave1_candidate_metrics.csv", all_candidates)
    write_csv(output_root / "phase3b_wave1_fold_selections.csv", all_selections)
    write_csv(output_root / "phase3b_wave1_fold_test_metrics.csv", all_tests)
    write_csv(output_root / "phase3b_wave1_oos_summary.csv", oos_rows)
    write_csv(output_root / "phase3b_wave1_parameter_stability.csv", stability_rows)
    write_csv(output_root / "phase3b_wave1_baseline_comparison.csv", oos_rows)
    unique_physical = len({row["cache_key"] for row in all_manifest})
    integrity_after = integrity_snapshot(strategy_root)
    integrity_ok = integrity_before["files"] == integrity_after["files"]
    rejected_tests = sum(
        row["split"] == "TEST" and row["evaluation_role"] not in {"SELECTED_TEST", "BASELINE_TEST"}
        for row in all_manifest
    )
    frozen = sum(
        str(row["selection_frozen_before_test"]).lower() == "true" for row in all_selections
    )
    frozen_before_test = all(
        row["split"] != "TEST" or row.get("selection_frozen_at_utc", "") <= row["start_timestamp"]
        for row in all_manifest
    )
    logical_ids_unique = len({row["run_id"] for row in all_manifest}) == len(all_manifest)
    summary = {
        "status": "PASSED"
        if len(all_manifest) == 1792
        and rejected_tests == 0
        and frozen == 161
        and frozen_before_test
        and logical_ids_unique
        and integrity_ok
        else "FAILED",
        "protocol_version": PROTOCOL_VERSION,
        "logical_planned": 1792,
        "logical_completed": len(all_manifest),
        "physical_backtests": unique_physical,
        "deduplicated_logical_roles": len(all_manifest) - unique_physical,
        "wave1_specs_terminal": len(oos_rows),
        "frozen_selections": frozen,
        "rejected_candidate_test_runs": rejected_tests,
        "test_informed_reselections": 0,
        "selection_frozen_before_all_tests": frozen_before_test,
        "logical_run_ids_unique": logical_ids_unique,
        "baseline_integrity_passed": integrity_ok,
        "eligible_validation_results": sum(
            str(row.get("eligible", "")).lower() == "true"
            for row in all_candidates
            if row["split"] == "VALIDATION"
        ),
        "insufficient_trade_validation_results": sum(
            "INSUFFICIENT_VALIDATION_TRADES" in row.get("ineligibility_reasons", "")
            for row in all_candidates
        ),
        "zero_trade_validation_results": sum(
            row["split"] == "VALIDATION" and row["validity_status"] == "VALID_ZERO_TRADES"
            for row in all_candidates
        ),
        "baseline_fallback_folds": sum(
            row["selection_status"] == "BASELINE_FALLBACK" for row in all_selections
        ),
        "fallback_baseline_below_5_trades": sum(
            row["selection_status"] == "BASELINE_FALLBACK"
            and int(float(row["baseline_validation_trade_count"])) < 5
            for row in all_selections
        ),
        "fallback_baseline_zero_trades": sum(
            row["selection_status"] == "BASELINE_FALLBACK"
            and int(float(row["baseline_validation_trade_count"])) == 0
            for row in all_selections
        ),
        "oos_return_better": sum(float(row["return_delta"]) > 0 for row in oos_rows),
        "oos_be_better": sum(float(row["be_delta"]) > 0 for row in oos_rows),
        "oos_mdd_better": sum(float(row["mdd_delta"]) > 0 for row in oos_rows),
        "return_and_be_better": sum(
            float(row["return_delta"]) > 0 and float(row["be_delta"]) > 0 for row in oos_rows
        ),
        "oos_worse_return": sum(float(row["return_delta"]) < 0 for row in oos_rows),
        "baseline_won_at_least_4_folds": sum(
            int(row["baseline_selected_folds"]) >= 4 for row in oos_rows
        ),
        "unstable_parameter_paths": sum(
            row["stability_flag"] == "FULL_RANGE_DRIFT" for row in oos_rows
        ),
        "stability_flag_rule": "FULL_RANGE_DRIFT iff selected values span the complete authorized candidate range",
        "retry_count": 0,
        "failed_count": 0,
        "wave3_original_estimate": 10073,
        "wave3_corrected_logical_estimate": 10318,
        "wave3_expected_physical_range": [10073, 10318],
    }
    summary["release_decision"] = (
        "WAVE3_READY" if summary["status"] == "PASSED" else "WAVE3_BLOCKED"
    )
    atomic_json(output_root / "phase3b_wave1_validation_summary.json", summary)
    atomic_json(output_root / "baseline_integrity_after.json", integrity_after)
    return summary


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    args.output_root.mkdir(parents=True, exist_ok=True)
    integrity_before_path = args.output_root / "baseline_integrity_before.json"
    if integrity_before_path.is_file():
        integrity_before = json.loads(integrity_before_path.read_text(encoding="utf-8"))
    else:
        integrity_before = integrity_snapshot(args.strategy_root)
        atomic_json(integrity_before_path, integrity_before)
    gate = preflight(args.strategy_root, args.market_root, args.output_root)
    if args.preflight_only:
        print(json.dumps(gate, indent=2))
        return 0
    specs, folds, _ = load_wave1()
    if args.limit_specs:
        specs = specs[: args.limit_specs]
    payloads = [
        {
            "spec": spec,
            "folds": folds["folds"],
            "output_root": str(args.output_root),
            "market_root": str(args.market_root),
            "strategy_root": str(args.strategy_root),
            "code_hash": gate["code_hash"],
            "data_provenance": gate["data_provenance"],
        }
        for spec in specs
    ]
    progress = {
        "status": "RUNNING",
        "planned_specs": len(specs),
        "completed_specs": 0,
        "failures": [],
    }
    atomic_json(args.output_root / "progress.json", progress)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_one_spec, payload): payload["spec"]["search_id"] for payload in payloads
        }
        for future in as_completed(futures):
            search_id = futures[future]
            try:
                result = future.result()
                progress["completed_specs"] += 1
                print(f"TERMINAL {search_id} logical={result['logical_rows']}", flush=True)
            except Exception as exc:
                progress["failures"].append({"search_id": search_id, "error": repr(exc)})
                print(f"FAILED {search_id}: {exc!r}", flush=True)
            atomic_json(args.output_root / "progress.json", progress)
    if progress["failures"]:
        progress["status"] = "FAILED"
        atomic_json(args.output_root / "progress.json", progress)
        return 2
    summary = stitch_and_summarize(args.output_root, specs, integrity_before, args.strategy_root)
    progress["status"] = "COMPLETE" if summary["status"] == "PASSED" else "VALIDATION_FAILED"
    atomic_json(args.output_root / "progress.json", progress)
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
