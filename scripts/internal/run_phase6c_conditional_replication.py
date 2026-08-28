#!/usr/bin/env python3
"""Run and report the frozen Phase 6C conditional cross-symbol falsification."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from data_engine.loader import load_events
from results.trade_episode import build_de_risk_episodes, write_episode_csv
from scripts.internal.build_phase4a_baseline_evaluation import drawdown, period_label
from scripts.internal.build_phase4b_cost_episode_audit import (
    COST_GRID,
    completed_episode_metrics,
    exact_be,
)
from scripts.internal.build_phase6a_expanded_screen import ROOT, inventory, sha256
from scripts.internal.run_phase4c_cross_symbol import (
    NOTIONAL_USDT,
    event_config,
    load_symbol,
    normalized_config_hash,
    run_case,
)


PHASE4C = ROOT / "outputs/baseline_evaluation/phase4c"
PHASE4C_RUNS = ROOT / "outputs/batches/phase4c_cross_symbol"
PHASE6A = ROOT / "outputs/baseline_evaluation/phase6a"
PHASE6B = ROOT / "outputs/baseline_evaluation/phase6b"
OUTPUT = ROOT / "outputs/baseline_evaluation/phase6c"
RUNS = ROOT / "outputs/batches/phase6c_cross_symbol"
DELIVERABLES = ROOT / "outputs/deliverables"
COMMON_START = "2024-07-01"
COMMON_END = "2026-06-30"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
REPLICATION = ("ETHUSDT", "SOLUSDT")
CANDIDATES = (
    "xlsx_s2_0124", "xlsx_s2_0265", "xlsx_s1_0003", "xlsx_s2_0278",
    "xlsx_s2_0364", "xlsx_s2_0285", "xlsx_s2_0435", "xlsx_s2_0669",
    "xlsx_s1_0453", "xlsx_s2_0158", "xlsx_s2_0256",
)
PHASE4C_OVERLAP = {"xlsx_s1_0003", "xlsx_s1_0453", "xlsx_s2_0435"}
PERIODS = ("2024H2", "2025H1", "2025H2", "2026H1")
LOPO_COSTS = (0.0, 0.10, 0.20, 0.30)
TOL = 1e-10


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def normalized_source(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def digest_text(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def strategy_hashes(strategy: str, contracts: str) -> dict[str, str]:
    package = ROOT / "strategies" / strategy
    source = normalized_source(package / "config.yaml")
    runtime_files = [path for path in sorted(package.glob("*.py")) if path.is_file()]
    ir = hashlib.sha256()
    for path in runtime_files:
        ir.update(path.name.encode()); ir.update(path.read_bytes())
    params = dict(source.get("params", {}))
    feature_fields = {key: value for key, value in params.items() if any(token in key.lower() for token in ("window", "period", "feature", "indicator", "timeframe"))}
    return {
        "canonical_parameter_hash": normalized_config_hash(source),
        "strategy_ir_hash": ir.hexdigest(),
        "semantic_contract_hashes": digest_text(sorted(filter(None, str(contracts).split(";")))),
        "feature_contract_hashes": digest_text(feature_fields),
    }


def protected_snapshot() -> dict[str, Any]:
    roots = [
        ROOT / "strategies", ROOT / "configs/semantic_contracts", PHASE4C, PHASE6A, PHASE6B,
        ROOT / "outputs/internal_audit/strategy_workbook/semantic_contracts",
        ROOT / "outputs/internal_audit/strategy_workbook/module_contracts",
    ]
    files: dict[str, dict[str, Any]] = {}
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            if "__pycache__" in path.parts:
                continue
            files[path.relative_to(ROOT).as_posix()] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for name, meta in sorted(files.items()):
        digest.update(f"{name}\0{meta['size']}\0{meta['sha256']}\n".encode())
    return {
        "content_file_count": len(files), "content_digest": digest.hexdigest(), "files": files,
        "data_inventories": {
            "market_data": inventory(ROOT / "historical_data/market_data"),
            "feature_data": inventory(ROOT / "historical_data/feature_data"),
        },
    }


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    names = set(before["files"]) | set(after["files"])
    changed = [name for name in names if before["files"].get(name) != after["files"].get(name)]
    if before["data_inventories"] != after["data_inventories"]:
        changed.append("historical_data_inventory")
    return sorted(changed)


def validate_market_data(market_root: Path) -> pd.DataFrame:
    rows = []
    expected_ns = 60_000_000_000
    for symbol in SYMBOLS:
        bars, funding, basic = load_symbol(market_root, symbol)
        times = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
        opens = np.fromiter((bar.open for bar in bars), dtype=float)
        highs = np.fromiter((bar.high for bar in bars), dtype=float)
        lows = np.fromiter((bar.low for bar in bars), dtype=float)
        closes = np.fromiter((bar.close for bar in bars), dtype=float)
        volumes = np.fromiter((bar.volume for bar in bars), dtype=float)
        duplicates = int(len(times) - len(np.unique(times)))
        missing = int(np.sum(np.diff(times) != expected_ns))
        ohlc_valid = bool(np.isfinite(np.c_[opens, highs, lows, closes]).all() and np.all(highs >= np.maximum.reduce([opens, lows, closes])) and np.all(lows <= np.minimum.reduce([opens, highs, closes])))
        volume_valid = bool(np.isfinite(volumes).all() and np.all(volumes >= 0))
        funding_count = len(funding)
        rows.append({
            "symbol": symbol, "common_start": COMMON_START, "common_end_exclusive": COMMON_END,
            "first_timestamp": basic["first_bar"], "last_timestamp": basic["last_bar"],
            "expected_bar_count": 1_049_760, "actual_bar_count": len(bars),
            "duplicate_timestamps": duplicates, "missing_intervals": missing,
            "timestamp_ordering": bool(np.all(np.diff(times) > 0)), "ohlc_valid": ohlc_valid,
            "volume_valid": volume_valid, "funding_records": funding_count,
            "funding_available": funding_count == 2_187,
            "status": "PASSED" if len(bars) == 1_049_760 and duplicates == 0 and missing == 0 and ohlc_valid and volume_valid and funding_count == 2_187 else "FAILED",
        })
    return pd.DataFrame(rows)


def parse_lag(value: Any) -> int:
    match = re.search(r"\d+", str(value))
    if match is None:
        raise ValueError(f"unparseable lag: {value}")
    return int(match.group())


def build_freeze(candidates: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    master = master.set_index("strategy_id")
    rows = []
    for candidate in candidates.itertuples(index=False):
        strategy = candidate.representative_strategy_id
        row = master.loc[strategy]
        hashes = strategy_hashes(strategy, candidate.contracts_applied)
        rows.append({
            "semantic_group_id": candidate.semantic_group_id,
            "representative_strategy_id": strategy,
            "member_strategy_ids": strategy,
            "phase6a_quality_tier": candidate.Phase6A_quality_tier,
            "phase6b_economic_label": candidate.phase6b_economic_label,
            "semantic_provenance": candidate.semantic_provenance,
            "provenance_tier": candidate.provenance_tier,
            "coverage_recovery_phase": candidate.coverage_recovery_phase,
            **hashes,
            "canonical_timeframe": row.canonical_timeframe,
            "canonical_realistic_lag": row.realistic_lag,
            "quantity_model": "continuous_research_notional",
            "exchange_step_size_rounding": False,
        })
    return pd.DataFrame(rows)


def run_path(root: Path, symbol: str, strategy: str, timeframe: str, lag: int) -> Path:
    return root / symbol / strategy / f"{timeframe}_lag{lag}m"


def reconstruct_btc_reference(strategy: str, coverage: pd.Series, freeze: pd.Series, destination: Path) -> None:
    source_path = Path(str(coverage.timeseries_path))
    frame = pd.read_parquet(source_path)
    start = int(pd.Timestamp(COMMON_START, tz="UTC").value); end = int(pd.Timestamp(COMMON_END, tz="UTC").value)
    frame = frame[(frame.event_time_ns >= start) & (frame.event_time_ns < end)].copy()
    if len(frame) != 1_049_760:
        raise ValueError(f"{strategy}: BTC common-window rows {len(frame)}")
    required = {"event_time_ns", "close", "normal_direction", "normal_total_return", "normal_turnover"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{strategy}: missing BTC columns {required - set(frame.columns)}")
    normalized = pd.DataFrame({
        "event_time_ns": frame.event_time_ns.astype("int64"), "close": frame.close.astype(float),
        "direction": frame.normal_direction.astype(float), "total_return": frame.normal_total_return.astype(float),
        "turnover": frame.normal_turnover.astype(float),
    })
    episodes, summary = build_de_risk_episodes(
        event_time_ns=normalized.event_time_ns, executed_position=normalized.direction,
        turnover_increment=normalized.turnover, gross_return_increment=normalized.total_return,
        strategy=strategy, symbol="BTCUSDT", granularity=str(freeze.canonical_timeframe),
        lag=str(freeze.canonical_realistic_lag), premium_mode="included", variant="original",
    )
    destination.mkdir(parents=True, exist_ok=True)
    tmp = destination / "timeseries.parquet.tmp"; normalized.to_parquet(tmp, index=False, compression="zstd"); os.replace(tmp, destination / "timeseries.parquet")
    write_episode_csv(destination / "per_trade_break_even.csv", episodes)
    atomic_json(destination / "summary.json", {
        "status": "RECONSTRUCTED_FROM_PHASE6A_CANONICAL_TIMESERIES", "strategy_id": strategy,
        "symbol": "BTCUSDT", "timeframe": freeze.canonical_timeframe,
        "lag_minutes": parse_lag(freeze.canonical_realistic_lag), "direction": "ORIGINAL", "premium": "INCLUDED",
        "common_start": COMMON_START, "common_end_exclusive": COMMON_END,
        "strategy_config_hash": freeze.canonical_parameter_hash, "semantic_parameter_changes": 0,
        "symbol_specific_parameter_changes": 0, "max_boundary_notional_error_usdt": 0.0,
        "notional_usdt": NOTIONAL_USDT, "instrument_precision_policy": "continuous research quantity; no exchange rounding",
        **summary,
    })


def quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q)) if len(values) else math.nan


def compute_case(path: Path, freeze: pd.Series, symbol: str, reused: bool) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    strategy = str(freeze.name)
    frame = pd.read_parquet(path / "timeseries.parquet")
    episodes = pd.read_csv(path / "per_trade_break_even.csv")
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    gross = frame.total_return.to_numpy(float); turnover = frame.turnover.to_numpy(float)
    total_return = float(gross.sum()); total_turnover = float(turnover.sum()); be = exact_be(total_return, total_turnover)
    frame["period"] = period_label(frame.event_time_ns)
    period_rows = []
    for label, child in frame.groupby("period", sort=True):
        if label not in PERIODS:
            continue
        inc = child.total_return.to_numpy(float); child_turn = float(child.turnover.sum()); child_return = float(inc.sum())
        if episodes.empty:
            episode_count = 0
        else:
            completion = pd.to_datetime(episodes.completion_timestamp, utc=True)
            episode_count = int((completion.map(lambda ts: f"{ts.year}H{1 if ts.month <= 6 else 2}") == label).sum())
        period_rows.append({"semantic_group_id": freeze.semantic_group_id, "representative_strategy_id": strategy, "symbol": symbol, "period": label, "Return": child_return, "Turnover": child_turn, "BE": exact_be(child_return, child_turn), "MDD": drawdown(inc), "Episode_Count": episode_count})
    period_frame = pd.DataFrame(period_rows)
    if tuple(period_frame.period) != PERIODS:
        raise ValueError(f"{strategy}/{symbol}: period mismatch {period_frame.period.tolist()}")
    be_values = episodes.break_even_bps.to_numpy(float) if not episodes.empty else np.array([])
    return_values = episodes.delta_gross_return.to_numpy(float) if not episodes.empty else np.array([])
    turnover_values = episodes.delta_turnover.to_numpy(float) if not episodes.empty else np.array([])
    durations = ((pd.to_datetime(episodes.completion_timestamp, utc=True) - pd.to_datetime(episodes.start_timestamp, utc=True)).dt.total_seconds().to_numpy(float) if not episodes.empty else np.array([]))
    _, concentration = completed_episode_metrics(episodes) if not episodes.empty else ({}, {})
    cost_rows = []
    period_cost_rows = []
    lopo: dict[float, tuple[bool, float]] = {}
    for cost in COST_GRID:
        net = gross - turnover * cost / 10_000.0
        period_net = period_frame.Return.to_numpy(float) - period_frame.Turnover.to_numpy(float) * cost / 10_000.0
        removed = np.array([period_net.sum() - value for value in period_net])
        lopo[cost] = (bool(np.all(removed > TOL)), float(removed.min()))
        cost_rows.append({"semantic_group_id": freeze.semantic_group_id, "representative_strategy_id": strategy, "symbol": symbol, "cost_bps": cost, "Return": float(net.sum()), "MDD": drawdown(net), "positive_period_count": int(np.sum(period_net > TOL)), "LOPO": bool(np.all(removed > TOL)), "minimum_LOPO_Return": float(removed.min()), "survival_flag": bool(net.sum() > TOL)})
        for child, value in zip(period_rows, period_net, strict=True):
            period_cost_rows.append({**child, "cost_bps": cost, "cost_adjusted_Return": float(value)})
    row = {
        "semantic_group_id": freeze.semantic_group_id, "representative_strategy_id": strategy, "symbol": symbol,
        "is_reference_BTC": symbol == "BTCUSDT", "semantic_provenance": freeze.semantic_provenance,
        "provenance_tier": freeze.provenance_tier, "coverage_phase": freeze.coverage_recovery_phase,
        "phase6a_quality_tier": freeze.phase6a_quality_tier, "phase6b_label": freeze.phase6b_economic_label,
        "common_start": COMMON_START, "common_end": COMMON_END, "Return": total_return, "Turnover": total_turnover,
        "BE": be, "MDD": drawdown(gross), "Episode_Count": len(episodes),
        "positive_period_count": int((period_frame.Return > TOL).sum()), "period_count": len(period_frame),
        "episode_BE_median": quantile(be_values, .5), "episode_BE_p25": quantile(be_values, .25),
        "episode_BE_positive_fraction": float(np.mean(be_values > TOL)) if len(be_values) else math.nan,
        "episode_BE_gt_0_10": float(np.mean(be_values > .10)) if len(be_values) else math.nan,
        "episode_BE_gt_0_20": float(np.mean(be_values > .20)) if len(be_values) else math.nan,
        "episode_BE_gt_0_30": float(np.mean(be_values > .30)) if len(be_values) else math.nan,
        "Return_0_10": total_return - total_turnover * .10 / 10_000.0,
        "Return_0_20": total_return - total_turnover * .20 / 10_000.0,
        "Return_0_30": total_return - total_turnover * .30 / 10_000.0,
        "Return_0_50": total_return - total_turnover * .50 / 10_000.0,
        "Return_1_00": total_return - total_turnover * 1.00 / 10_000.0,
        "LOPO_0": lopo[0.0][0], "LOPO_0_10": lopo[.10][0], "LOPO_0_20": lopo[.20][0], "LOPO_0_30": lopo[.30][0],
        "minimum_LOPO_0": lopo[0.0][1], "minimum_LOPO_0_10": lopo[.10][1], "minimum_LOPO_0_20": lopo[.20][1], "minimum_LOPO_0_30": lopo[.30][1],
        "top1pct_positive_return_share": concentration.get("top1pct_positive_return_share", math.nan),
        "top5pct_positive_return_share": concentration.get("top5pct_positive_return_share", math.nan),
        "Return_without_top1pct": concentration.get("return_without_best1pct", math.nan),
        "BE_without_top1pct": concentration.get("BE_without_best1pct", math.nan),
        "Return_without_top5pct": concentration.get("return_without_best5pct", math.nan),
        "BE_without_top5pct": concentration.get("BE_without_best5pct", math.nan),
        "holding_duration_median": quantile(durations, .5), "holding_duration_p95": quantile(durations, .95),
        "result_reused_from_phase4c": reused, "physical_run_id": str(path),
        "strategy_parameter_hash": freeze.canonical_parameter_hash, "strategy_ir_hash": freeze.strategy_ir_hash,
        "BE_crossing_residual": abs(total_return - total_turnover * be / 10_000.0) if total_turnover else 0.0,
        "episode_removal_residual": max(
            abs(concentration.get("return_without_best1pct", 0.0) - concentration.get("turnover_without_best1pct", 0.0) * concentration.get("BE_without_best1pct", 0.0) / 10_000.0) if "turnover_without_best1pct" in concentration else 0.0,
            abs(concentration.get("return_without_best5pct", 0.0) - concentration.get("turnover_without_best5pct", 0.0) * concentration.get("BE_without_best5pct", 0.0) / 10_000.0) if "turnover_without_best5pct" in concentration else 0.0,
        ),
        "episode_metric_rows": len(episodes), "maximum_episode_BE_residual": float(np.max(np.abs(return_values - turnover_values * be_values / 10_000.0))) if len(episodes) else 0.0,
        "notional_usdt": summary.get("notional_usdt", NOTIONAL_USDT), "max_boundary_notional_error_usdt": summary.get("max_boundary_notional_error_usdt", 0.0),
    }
    episode_row = {
        "semantic_group_id": freeze.semantic_group_id, "representative_strategy_id": strategy, "symbol": symbol,
        "episode_count": len(episodes), "BE_mean": float(np.mean(be_values)) if len(be_values) else math.nan,
        "BE_median": quantile(be_values, .5), "BE_P10": quantile(be_values, .10), "BE_P25": quantile(be_values, .25),
        "BE_P75": quantile(be_values, .75), "BE_P90": quantile(be_values, .90), "BE_P95": quantile(be_values, .95),
        "BE_positive_fraction": float(np.mean(be_values > 0)) if len(be_values) else math.nan,
        "BE_gt_0_10_fraction": float(np.mean(be_values > .10)) if len(be_values) else math.nan,
        "BE_gt_0_20_fraction": float(np.mean(be_values > .20)) if len(be_values) else math.nan,
        "BE_gt_0_30_fraction": float(np.mean(be_values > .30)) if len(be_values) else math.nan,
        "BE_gt_0_50_fraction": float(np.mean(be_values > .50)) if len(be_values) else math.nan,
        "BE_gt_1_00_fraction": float(np.mean(be_values > 1.0)) if len(be_values) else math.nan,
        "Return_P10": quantile(return_values, .10), "Return_P25": quantile(return_values, .25), "Return_median": quantile(return_values, .5), "Return_P75": quantile(return_values, .75), "Return_P90": quantile(return_values, .90),
        "holding_duration_median_seconds": quantile(durations, .5), "holding_duration_P75_seconds": quantile(durations, .75), "holding_duration_P90_seconds": quantile(durations, .90), "holding_duration_P95_seconds": quantile(durations, .95), "holding_duration_P99_seconds": quantile(durations, .99), "holding_duration_max_seconds": float(durations.max()) if len(durations) else math.nan,
        "top1pct_positive_return_share": concentration.get("top1pct_positive_return_share", math.nan), "top5pct_positive_return_share": concentration.get("top5pct_positive_return_share", math.nan),
        "Return_without_top1pct": concentration.get("return_without_best1pct", math.nan), "BE_without_top1pct": concentration.get("BE_without_best1pct", math.nan), "Return_without_top5pct": concentration.get("return_without_best5pct", math.nan), "BE_without_top5pct": concentration.get("BE_without_best5pct", math.nan),
    }
    return row, period_rows, episode_row, cost_rows


def label_candidate(group: pd.DataFrame) -> dict[str, Any]:
    strategy = group.representative_strategy_id.iloc[0]
    by_symbol = group.set_index("symbol")
    eth, sol, btc = by_symbol.loc["ETHUSDT"], by_symbol.loc["SOLUSDT"], by_symbol.loc["BTCUSDT"]
    eth_pos = bool(eth.Return > TOL and eth.BE > TOL); sol_pos = bool(sol.Return > TOL and sol.BE > TOL)
    positive = int(eth_pos) + int(sol_pos)
    eth_cost = bool(eth.Return_0_10 > TOL); sol_cost = bool(sol.Return_0_10 > TOL)
    eth_broad = bool(eth.episode_BE_median > TOL and eth.episode_BE_positive_fraction > .5)
    sol_broad = bool(sol.episode_BE_median > TOL and sol.episode_BE_positive_fraction > .5)
    if positive == 2 and eth_cost and sol_cost:
        label = "CONDITIONAL_BROAD_REPLICATION"
    elif positive == 2:
        # The requested taxonomy omitted the empirically possible case where
        # both zero-cost replications are positive but the 0.10-bps condition
        # fails.  Keep it explicit rather than misreporting it as 0/2.
        label = "BOTH_MARKETS_POSITIVE_COST_UNSUPPORTED"
    elif positive == 1:
        label = "PARTIAL_REPLICATION"
    else:
        label = "BTC_SPECIFIC_OR_NONREPLICATING"
    high = label == "CONDITIONAL_BROAD_REPLICATION" and bool(btc.Return_0_10 > TOL)
    plus = high and all(row.episode_BE_median > TOL and row.episode_BE_positive_fraction > .5 for _, row in by_symbol.iterrows())
    conditional = label == "PARTIAL_REPLICATION" and (eth_cost or sol_cost)
    concentrated = [bool((row.Return_without_top5pct <= TOL) or (row.BE_without_top5pct <= TOL)) for _, row in by_symbol.iterrows()]
    warnings = ["WINNER_CONCENTRATION_PERSISTS"] if all(concentrated) else []
    if int(btc.Episode_Count) <= 1:
        warnings.append("EXTREME_LOW_EPISODE_COUNT")
    return {
        "strategy_id": strategy, "semantic_group_id": btc.semantic_group_id,
        "provenance": btc.provenance_tier, "phase6a_tier": btc.phase6a_quality_tier, "phase6b_label": btc.phase6b_label,
        "BTC_Return": btc.Return, "BTC_BE": btc.BE, "ETH_Return": eth.Return, "ETH_BE": eth.BE, "SOL_Return": sol.Return, "SOL_BE": sol.BE,
        "positive_nonBTC_markets": positive, "cost_supported_nonBTC_markets": int(eth_cost) + int(sol_cost),
        "ETH_survive_0_10": eth_cost, "SOL_survive_0_10": sol_cost, "ETH_episode_broad": eth_broad, "SOL_episode_broad": sol_broad,
        "winner_concentration_pattern": "REPLICATES" if all(concentrated) else "MIXED",
        "replication_label": label, "phase6d_high_priority": high, "phase6d_priority_plus": plus,
        "phase6d_conditional": conditional, "warnings": ";".join(warnings) or "NONE",
    }


def heatmap(master: pd.DataFrame, column: str, title: str, filename: Path, binary: bool = False) -> None:
    pivot = master.pivot(index="representative_strategy_id", columns="symbol", values=column).reindex(index=CANDIDATES, columns=SYMBOLS)
    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    values = pivot.to_numpy(float)
    limit = 1 if binary else max(float(np.nanmax(np.abs(values))), 1e-12)
    image = ax.imshow(values, aspect="auto", cmap="RdYlGn", vmin=0 if binary else -limit, vmax=1 if binary else limit)
    ax.set_xticks(range(3), SYMBOLS); ax.set_yticks(range(len(pivot)), pivot.index, fontsize=8); ax.set_title(title)
    fig.colorbar(image, ax=ax); fig.tight_layout(); fig.savefig(filename, dpi=165); plt.close(fig)


def make_figures(output: Path, master: pd.DataFrame, summary: pd.DataFrame) -> None:
    root = output / "figures"; root.mkdir(parents=True, exist_ok=True)
    heatmap(master, "Return", "Conditional Cross-Symbol Return (1x)", root / "01_return_heatmap.png")
    heatmap(master, "BE", "Signed Global BE (bps)", root / "02_be_heatmap.png")
    heatmap(master, "Return_0_10", "Return at 0.10 bps Total Cost", root / "03_return_010_heatmap.png")
    heatmap(master, "episode_BE_median", "Median Episode BE (bps)", root / "04_episode_median_be_heatmap.png")
    heatmap(master, "episode_BE_positive_fraction", "Fraction Episodes with Positive BE", root / "05_episode_positive_fraction.png", binary=True)
    concentration = master.copy(); concentration["winner_concentrated"] = (concentration.Return_without_top5pct <= TOL) | (concentration.BE_without_top5pct <= TOL)
    heatmap(concentration, "winner_concentrated", "Winner Concentration by Symbol", root / "06_winner_concentration.png", binary=True)
    table = pd.crosstab(summary.provenance, summary.replication_label)
    fig, ax = plt.subplots(figsize=(10, 5)); table.plot.bar(stacked=True, ax=ax); ax.set(ylabel="Candidate groups", title="Replication Label by Provenance"); fig.tight_layout(); fig.savefig(root / "07_labels_by_provenance.png", dpi=165); plt.close(fig)
    for strategy in summary.loc[summary.phase6d_high_priority | summary.phase6d_conditional, "strategy_id"]:
        child = master[master.representative_strategy_id == strategy].set_index("symbol").reindex(SYMBOLS)
        fig, axes = plt.subplots(2, 3, figsize=(14, 8))
        for axis, (column, title) in zip(axes.flat, (("Return", "Return"), ("BE", "BE bps"), ("Return_0_10", "Return at 0.10 bps"), ("episode_BE_median", "Episode median BE"), ("episode_BE_positive_fraction", "Positive episode fraction"), ("top5pct_positive_return_share", "Top 5% positive Return share")), strict=True):
            axis.bar(SYMBOLS, child[column], color=["#2563eb", "#94a3b8", "#94a3b8"]); axis.axhline(0, color="black", lw=.7); axis.set_title(title)
        fig.suptitle(f"{strategy} — conditional cross-symbol falsification"); fig.tight_layout(); fig.savefig(root / f"{strategy}_phase6d_candidate.png", dpi=150); plt.close(fig)


def aggregate_summary(summary: pd.DataFrame, column: str) -> pd.DataFrame:
    rows = []
    for value, child in summary.groupby(column, dropna=False):
        rows.append({column: value, "candidate_groups": len(child), "conditional_broad_replication": int((child.replication_label == "CONDITIONAL_BROAD_REPLICATION").sum()), "partial_replication": int((child.replication_label == "PARTIAL_REPLICATION").sum()), "nonreplication": int((child.replication_label == "BTC_SPECIFIC_OR_NONREPLICATING").sum()), "both_nonBTC_survive_0_10": int((child.cost_supported_nonBTC_markets == 2).sum()), "both_nonBTC_episode_broad": int((child.ETH_episode_broad & child.SOL_episode_broad).sum()), "phase6d_high_priority": int(child.phase6d_high_priority.sum()), "phase6d_priority_plus": int(child.phase6d_priority_plus.sum())})
    return pd.DataFrame(rows)


def write_html(output: Path, summary: pd.DataFrame, master: pd.DataFrame, validation: dict[str, Any]) -> None:
    counts = summary.replication_label.value_counts()
    body = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 6C Cross-Symbol Falsification</title><style>body{{font-family:Arial;margin:28px;max-width:1500px}}table{{border-collapse:collapse;font-size:11px}}th,td{{border:1px solid #ddd;padding:5px}}img{{max-width:49%;vertical-align:top}}.cards{{padding:14px;background:#f1f5f9}}</style></head><body><h1>Phase 6C — Conditional Cross-Symbol Falsification</h1><div class='cards'>Candidates: 11 | Markets: ETHUSDT, SOLUSDT | Broad conditional: {counts.get('CONDITIONAL_BROAD_REPLICATION',0)} | Partial: {counts.get('PARTIAL_REPLICATION',0)} | Nonreplicating: {counts.get('BTC_SPECIFIC_OR_NONREPLICATING',0)} | Both survive 0.10 bps: {(summary.cost_supported_nonBTC_markets==2).sum()} | Episode breadth both: {(summary.ETH_episode_broad & summary.SOL_episode_broad).sum()} | Phase6D high: {summary.phase6d_high_priority.sum()} | plus: {summary.phase6d_priority_plus.sum()} | conditional: {summary.phase6d_conditional.sum()}</div><p>This is falsification evidence on two frozen replication markets, not validation or production approval.</p><h2>Candidate summary</h2>{summary.to_html(index=False, float_format=lambda x:f'{x:.6g}')}<h2>Figures</h2>"""
    for name in ("01_return_heatmap.png", "02_be_heatmap.png", "03_return_010_heatmap.png", "04_episode_median_be_heatmap.png", "05_episode_positive_fraction.png", "06_winner_concentration.png", "07_labels_by_provenance.png"):
        body += f"<img src='figures/{name}'>"
    high = master[master.representative_strategy_id.isin(summary.loc[summary.phase6d_high_priority | summary.phase6d_conditional, "strategy_id"])]
    body += f"<h2>Follow-up evidence</h2>{high.to_html(index=False, float_format=lambda x:f'{x:.6g}')}<h2>Validation</h2><pre>{html.escape(json.dumps(validation, indent=2))}</pre></body></html>"
    (output / "phase6c_cross_symbol_falsification_review.html").write_text(body, encoding="utf-8")


def package(output: Path) -> tuple[Path, str, int, int]:
    target = DELIVERABLES / "phase6c_cross_symbol_falsification.zip"; temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase6c_delivery.json":
                archive.write(path, Path("phase6c_cross_symbol_falsification") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip(); members = len(archive.infolist())
    if bad:
        raise RuntimeError(f"ZIP integrity failed: {bad}")
    return target, sha256(target), members, target.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    parser.add_argument("--run-root", type=Path, default=RUNS)
    args = parser.parse_args(); args.output_root.mkdir(parents=True, exist_ok=True); args.run_root.mkdir(parents=True, exist_ok=True)
    candidates = pd.read_csv(PHASE6B / "phase6b_phase6c_candidates.csv")
    if tuple(candidates.representative_strategy_id) != CANDIDATES or len(candidates) != 11:
        raise ValueError("Phase 6C candidate freeze mismatch")
    phase6a_master = pd.read_csv(PHASE6A / "phase6a_strategy_master.csv")
    coverage = pd.read_csv(PHASE6A / "phase6a_baseline_result_coverage.csv").set_index("strategy_id")
    freeze = build_freeze(candidates, phase6a_master)
    atomic_csv(args.output_root / "phase6c_candidate_freeze.csv", freeze)
    before = protected_snapshot(); atomic_json(args.output_root / "phase6c_protected_hashes_before.json", before)
    data = validate_market_data(args.market_root); atomic_csv(args.output_root / "phase6c_data_integrity.csv", data)
    if not data.status.eq("PASSED").all():
        raise RuntimeError("Phase 6C data-integrity pre-gate failed")
    freeze_index = freeze.set_index("representative_strategy_id")
    reuse_rows = []
    run_rows = []
    for strategy in CANDIDATES:
        f = freeze_index.loc[strategy]; timeframe = str(f.canonical_timeframe); lag = parse_lag(f.canonical_realistic_lag)
        btc_destination = run_path(args.run_root, "BTCUSDT", strategy, timeframe, lag)
        if strategy in PHASE4C_OVERLAP:
            btc_path = run_path(PHASE4C_RUNS, "BTCUSDT", strategy, timeframe, lag)
            btc_reused = True
        else:
            btc_path = btc_destination; btc_reused = False
            if not (btc_path / "summary.json").is_file():
                reconstruct_btc_reference(strategy, coverage.loc[strategy], f, btc_path)
        run_rows.append({"strategy_id": strategy, "symbol": "BTCUSDT", "logical_role": "REFERENCE_RECONSTRUCTION", "physical_status": "REUSED_PHASE4C" if btc_reused else "RECONSTRUCTED_PHASE6A_NO_BACKTEST", "path": str(btc_path)})
        for symbol in REPLICATION:
            old = run_path(PHASE4C_RUNS, symbol, strategy, timeframe, lag)
            destination = run_path(args.run_root, symbol, strategy, timeframe, lag)
            reusable = strategy in PHASE4C_OVERLAP and (old / "summary.json").is_file() and (old / "timeseries.parquet").is_file() and (old / "per_trade_break_even.csv").is_file()
            reuse_status = "NOT_OVERLAP"
            if reusable:
                old_summary = json.loads((old / "summary.json").read_text(encoding="utf-8"))
                identity = old_summary["strategy_config_hash"] == f.canonical_parameter_hash and old_summary["timeframe"] == timeframe and int(old_summary["lag_minutes"]) == lag and old_summary["premium"] == "INCLUDED" and old_summary["common_start"] == COMMON_START and old_summary["common_end_exclusive"] == COMMON_END
                reusable = bool(identity); reuse_status = "IDENTITY_PASSED" if identity else "IDENTITY_FAILED"
            path = old if reusable else destination
            if not reusable and not (path / "summary.json").is_file():
                bars, funding, _ = load_symbol(args.market_root, symbol)
                run_case(strategy=strategy, symbol=symbol, frequency=timeframe, lag_minutes=lag, config_hash=f.canonical_parameter_hash, bars=bars, funding=funding, output=path)
                status = "NEW_PHYSICAL_BACKTEST"
            elif reusable:
                status = "REUSED_PHASE4C"
            else:
                status = "RESUMED_EXISTING_PHASE6C"
            reuse_rows.append({"strategy_id": strategy, "symbol": symbol, "phase4c_overlap": strategy in PHASE4C_OVERLAP, "identity_status": reuse_status, "reused": reusable, "canonical_parameter_hash": f.canonical_parameter_hash, "strategy_ir_hash": f.strategy_ir_hash, "timeframe": timeframe, "lag": f"lag{lag}m", "premium": "INCLUDED", "common_start": COMMON_START, "common_end": COMMON_END, "source_path": str(path)})
            run_rows.append({"strategy_id": strategy, "symbol": symbol, "logical_role": "REPLICATION", "physical_status": status, "path": str(path)})
            atomic_csv(args.run_root / "phase6c_run_manifest.csv", pd.DataFrame(run_rows))
    reuse = pd.DataFrame(reuse_rows); atomic_csv(args.output_root / "phase6c_phase4c_reuse_manifest.csv", reuse)
    result_rows = []; period_rows = []; episode_rows = []; stress_rows = []
    path_lookup = {(row["strategy_id"], row["symbol"]): Path(row["path"]) for row in run_rows}
    for strategy in CANDIDATES:
        f = freeze_index.loc[strategy]
        for symbol in SYMBOLS:
            reused = bool(symbol != "BTCUSDT" and reuse[(reuse.strategy_id == strategy) & (reuse.symbol == symbol)].reused.iloc[0])
            row, periods, episode, stress = compute_case(path_lookup[(strategy, symbol)], f, symbol, reused)
            result_rows.append(row); period_rows.extend(periods); episode_rows.append(episode); stress_rows.extend(stress)
    master = pd.DataFrame(result_rows); periods = pd.DataFrame(period_rows); episodes = pd.DataFrame(episode_rows); stress = pd.DataFrame(stress_rows)
    summary = pd.DataFrame(label_candidate(group) for _, group in master.groupby("representative_strategy_id", sort=False)).set_index("strategy_id").loc[list(CANDIDATES)].reset_index()
    phase4c_results = pd.read_csv(PHASE4C / "phase4c_cross_symbol_results.csv")
    invariance_rows = []
    for strategy in PHASE4C_OVERLAP:
        for symbol in SYMBOLS:
            current = master[(master.representative_strategy_id == strategy) & (master.symbol == symbol)].iloc[0]
            old = phase4c_results[(phase4c_results.representative_strategy_id == strategy) & (phase4c_results.symbol == symbol)].iloc[0]
            invariance_rows.append({"strategy_id": strategy, "symbol": symbol, "Return_residual": abs(current.Return - old.Return), "Turnover_residual": abs(current.Turnover - old.Turnover), "BE_residual": abs(current.BE - old.BE), "MDD_residual": abs(current.MDD - old.MDD), "Episode_Count_residual": abs(current.Episode_Count - old.episode_count), "episode_BE_fraction_residual": abs(current.episode_BE_positive_fraction - old.episode_BE_positive_fraction), "winner_top1_residual": abs(current.top1pct_positive_return_share - old.winner_concentration_top1pct), "holding_duration_median_residual": abs(current.holding_duration_median / 60 - old.holding_duration_median), "cost_010_residual": abs(current.Return_0_10 - old.return_0_10bps)})
    invariance = pd.DataFrame(invariance_rows)
    phase6d = summary[summary.phase6d_high_priority | summary.phase6d_priority_plus | summary.phase6d_conditional].copy()
    phase6d["followup_class"] = np.select([phase6d.phase6d_priority_plus, phase6d.phase6d_high_priority, phase6d.phase6d_conditional], ["PHASE6D_PRIORITY_PLUS", "PHASE6D_HIGH_PRIORITY", "PHASE6D_CONDITIONAL"], default="")
    phase6d["why_execution_realism_study_is_justified"] = "frozen cross-symbol replication evidence merits execution-realism falsification"
    phase6d["remaining_warnings"] = phase6d.warnings
    nonrep = summary[summary.replication_label == "BTC_SPECIFIC_OR_NONREPLICATING"].copy()
    atomic_csv(args.output_root / "phase6c_phase4c_invariance.csv", invariance)
    atomic_csv(args.output_root / "phase6c_cross_symbol_master.csv", master)
    atomic_csv(args.output_root / "phase6c_replication_summary.csv", summary)
    atomic_csv(args.output_root / "phase6c_cross_symbol_cost_stress.csv", stress)
    atomic_csv(args.output_root / "phase6c_episode_replication.csv", episodes)
    atomic_csv(args.output_root / "phase6c_symbol_period_results.csv", periods)
    atomic_csv(args.output_root / "phase6c_provenance_replication.csv", aggregate_summary(summary, "provenance"))
    coverage_summary = summary.merge(freeze[["representative_strategy_id", "coverage_recovery_phase"]], left_on="strategy_id", right_on="representative_strategy_id").drop(columns="representative_strategy_id")
    atomic_csv(args.output_root / "phase6c_coverage_phase_replication.csv", aggregate_summary(coverage_summary, "coverage_recovery_phase"))
    atomic_csv(args.output_root / "phase6c_phase6d_candidates.csv", phase6d)
    atomic_csv(args.output_root / "phase6c_nonreplicating_candidates.csv", nonrep)
    make_figures(args.output_root, master, summary)
    after = protected_snapshot(); atomic_json(args.output_root / "phase6c_protected_hashes_after.json", after)
    changed = compare_snapshots(before, after)
    reused_count = int(reuse.reused.sum())
    # Every non-reused non-BTC case is a Phase 6C physical result.  On resume it
    # is intentionally reported as existing rather than executed twice.
    new_physical = int((~reuse.reused).sum())
    validations = {
        "status": "PASSED", "candidate_groups": 11, "terminal_candidate_groups": len(summary),
        "logical_nonBTC_replication_cases": 22, "terminal_nonBTC_cases": len(reuse),
        "phase4c_reused_cases": reused_count, "new_physical_nonBTC_backtests": new_physical, "new_BTC_backtests": 0,
        "replication_symbols": list(REPLICATION), "common_start": COMMON_START, "common_end_exclusive": COMMON_END,
        "parameter_hash_variants_max": int(master.groupby("representative_strategy_id").strategy_parameter_hash.nunique().max()),
        "strategy_ir_hash_variants_max": int(master.groupby("representative_strategy_id").strategy_ir_hash.nunique().max()),
        "maximum_BE_crossing_residual": float(master.BE_crossing_residual.max()),
        "maximum_episode_BE_residual": float(master.maximum_episode_BE_residual.max()),
        "maximum_phase4c_Return_residual": float(invariance.Return_residual.max()),
        "maximum_phase4c_Turnover_residual": float(invariance.Turnover_residual.max()),
        "maximum_phase4c_BE_residual": float(invariance.BE_residual.max()),
        "maximum_phase4c_MDD_residual": float(invariance.MDD_residual.max()),
        "maximum_boundary_notional_error_usdt": float(master.max_boundary_notional_error_usdt.max()),
        "parameter_optimization_runs": 0, "target_market_parameter_changes": 0, "new_semantic_policies": 0,
        "production_configs_created": 0, "replication_symbol_additions_after_performance": 0,
        "protected_artifact_changes": len(changed), "protected_changed_paths": changed,
        "conditional_broad_replication": int((summary.replication_label == "CONDITIONAL_BROAD_REPLICATION").sum()),
        "both_markets_positive_cost_unsupported": int((summary.replication_label == "BOTH_MARKETS_POSITIVE_COST_UNSUPPORTED").sum()),
        "partial_replication": int((summary.replication_label == "PARTIAL_REPLICATION").sum()),
        "nonreplicating": int((summary.replication_label == "BTC_SPECIFIC_OR_NONREPLICATING").sum()),
        "phase6d_high_priority": int(summary.phase6d_high_priority.sum()), "phase6d_priority_plus": int(summary.phase6d_priority_plus.sum()), "phase6d_conditional": int(summary.phase6d_conditional.sum()),
    }
    gates = [len(summary) == 11, len(reuse) == 22, reused_count == 6, new_physical in (0, 16), data.status.eq("PASSED").all(), validations["parameter_hash_variants_max"] == 1, validations["strategy_ir_hash_variants_max"] == 1, validations["maximum_BE_crossing_residual"] <= TOL, validations["maximum_episode_BE_residual"] <= TOL, len(changed) == 0]
    if not all(gates): validations["status"] = "FAILED"
    atomic_json(args.output_root / "phase6c_validation_summary.json", validations)
    write_html(args.output_root, summary, master, validations)
    if validations["status"] != "PASSED": raise RuntimeError(f"Phase 6C completion gate failed: {validations}")
    archive, digest, members, size = package(args.output_root)
    atomic_json(args.output_root / "phase6c_delivery.json", {"server_path": str(archive), "sha256": digest, "members": members, "size_bytes": size, "zip_integrity": "PASSED"})
    print(json.dumps({"status": "PASSED", "reused": reused_count, "new_physical": new_physical, "labels": summary.replication_label.value_counts().to_dict(), "phase6d": phase6d.followup_class.value_counts().to_dict(), "zip": str(archive), "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
