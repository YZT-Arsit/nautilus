#!/usr/bin/env python3
"""
Package the completed P1 dynamic-breakout cross-symbol replication.

This is a post-processor only. It reuses four full-window STRICT_REVERSE
reruns and four earlier actual split-path reruns; it never runs a strategy.
Metrics unavailable from the older split artifacts are left blank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


plt.switch_backend("Agg")

ROOT = Path(__file__).resolve().parents[2]
STRATEGY, TIMEFRAME = "dynamic_breakout_short", "1m"
EVIDENCE = "HISTORICAL_CROSS_SYMBOL_REPLICATION"
SYMBOLS = (
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "SUIUSDT",
    "1000PEPEUSDT",
)
NEW = {"ETHUSDT", "BNBUSDT", "ADAUSDT", "1000PEPEUSDT"}
EXISTING = set(SYMBOLS) - NEW
CASE_KEY = "case_ab91c24774f49d2f7bb5"


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    os.replace(tmp, path)


def atomic_json(value: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def normal_master(repo: Path) -> pd.DataFrame:
    path = (
        repo
        / "outputs/deliverables/multi_symbol_classification/multi_symbol_classification_master.csv"
    )
    frame = pd.read_csv(path)
    frame = frame[
        frame.strategy_id.eq(STRATEGY) & frame.timeframe.eq(TIMEFRAME) & frame.symbol.isin(SYMBOLS)
    ].copy()
    if len(frame) != 8 or frame.symbol.nunique() != 8:
        raise ValueError(f"expected 8 NORMAL rows, found {len(frame)}")
    return frame.set_index("symbol")


def normal_review(repo: Path, symbol: str) -> Path:
    return (
        repo
        / "outputs/baseline_evaluation/tick_review_stageA_9symbols_preworkbook/matrix_cases"
        / f"symbol={symbol}"
        / "timeframe=1m/strategy=dynamic_breakout_short/review_timeseries.parquet"
    )


def new_reverse(research: Path, symbol: str) -> Path:
    return (
        research
        / "reverse_cases"
        / f"symbol={symbol}"
        / "timeframe=1m/strategy=dynamic_breakout_short"
    )


def daily_from_review(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(
        path, columns=["event_time_ns", "cumulative_return_with_premium", "cumulative_turnover"]
    )
    times = pd.DatetimeIndex(pd.to_datetime(frame.event_time_ns, unit="ns", utc=True))
    daily = (
        pd.DataFrame(
            {
                "cum_return": frame.cumulative_return_with_premium.to_numpy(float),
                "cum_turnover": frame.cumulative_turnover.to_numpy(float),
            },
            index=times,
        )
        .groupby(times.floor("D"))
        .last()
    )
    daily["Return"] = daily.cum_return.diff()
    daily["Turnover_raw"] = daily.cum_turnover.diff()
    daily.iloc[0, daily.columns.get_loc("Return")] = daily.cum_return.iloc[0]
    daily.iloc[0, daily.columns.get_loc("Turnover_raw")] = daily.cum_turnover.iloc[0]
    return daily


def block_from_daily(daily: pd.DataFrame, block: str, start: str, end: str) -> dict[str, object]:
    part = daily[(daily.index >= start) & (daily.index < end)]
    value, turnover, std = (
        float(part.Return.sum()),
        float(part.Turnover_raw.sum()),
        float(part.Return.std(ddof=1)),
    )
    return {
        "block": block,
        "start": start,
        "end_exclusive": end,
        "n_daily_observations": len(part),
        "Return": value,
        "Sharpe": float(part.Return.mean() / std * math.sqrt(365)) if std else math.nan,
        "Signed_BE_bps": value * 10_000 / turnover if turnover else math.nan,
        "MaxDD": pd.NA,
        "Turnover_raw": turnover,
    }


def yearly_rows(repo: Path, research: Path, old: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    comparison = (
        repo
        / "outputs/deliverables/strategy_execution_reverse_review/reverse_validation/reverse_case_comparison.csv"
    )
    for symbol in SYMBOLS:
        if symbol in EXISTING:
            item = old.loc[symbol]
            blocks = (
                ("Y1", "_DISCOVERY", item.discovery_start, item.discovery_end_exclusive),
                ("Y2", "", item.validation_start, item.validation_end_exclusive),
            )
            for block, suffix, start, end in blocks:
                for variant, prefix in (("NORMAL", "NORMAL"), ("STRICT_REVERSE", "REVERSE")):
                    rows.append(
                        {
                            "strategy_id": STRATEGY,
                            "symbol": symbol,
                            "timeframe": TIMEFRAME,
                            "direction_variant": variant,
                            "evidence_class": EVIDENCE,
                            "block": block,
                            "start": start,
                            "end_exclusive": end,
                            "n_daily_observations": 365 if block == "Y1" else 364,
                            "Return": item[f"Return_{prefix}{suffix}"],
                            "Sharpe": item[f"Sharpe_{prefix}{suffix}"],
                            "Signed_BE_bps": item[f"Signed_BE_bps_{prefix}{suffix}"],
                            "MaxDD": item[f"MaxDD_{prefix}{suffix}"],
                            "Turnover_raw": item[f"Turnover_raw_{prefix}{suffix}"],
                            "metric_source": "ACTUAL_SPLIT_RERUN_SUMMARY",
                            "source_path": rel(repo, comparison),
                        }
                    )
        else:
            normal_path = normal_review(repo, symbol)
            daily = daily_from_review(normal_path)
            for item in (
                block_from_daily(daily, "Y1", "2024-07-01", "2025-07-01"),
                block_from_daily(daily, "Y2", "2025-07-01", "2026-06-30"),
            ):
                rows.append(
                    {
                        "strategy_id": STRATEGY,
                        "symbol": symbol,
                        "timeframe": TIMEFRAME,
                        "direction_variant": "NORMAL",
                        "evidence_class": EVIDENCE,
                        **item,
                        "metric_source": "ACTUAL_REVIEW_DAILY_ENDPOINTS",
                        "source_path": rel(repo, normal_path),
                    }
                )
            summary_path = new_reverse(research, symbol) / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            for item in summary["yearly_blocks"]:
                rows.append(
                    {
                        "strategy_id": STRATEGY,
                        "symbol": symbol,
                        "timeframe": TIMEFRAME,
                        "direction_variant": "STRICT_REVERSE",
                        "evidence_class": EVIDENCE,
                        **item,
                        "metric_source": "ACTUAL_FULL_WINDOW_RERUN_SUMMARY",
                        "source_path": rel(repo, summary_path),
                    }
                )
    result = (
        pd.DataFrame(rows)
        .sort_values(["symbol", "direction_variant", "block"])
        .reset_index(drop=True)
    )
    if len(result) != 32 or not result.groupby(["symbol", "direction_variant"]).size().eq(2).all():
        raise ValueError("yearly table is not 8x2x2")
    return result


def paired_rows(
    repo: Path, research: Path, normal: pd.DataFrame, yearly: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    master = (
        repo
        / "outputs/deliverables/multi_symbol_classification/multi_symbol_classification_master.csv"
    )
    comparison = (
        repo
        / "outputs/deliverables/strategy_execution_reverse_review/reverse_validation/reverse_case_comparison.csv"
    )
    for symbol in SYMBOLS:
        item = normal.loc[symbol]
        common = {
            "strategy_id": STRATEGY,
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "evidence_class": EVIDENCE,
            "effective_start": item.effective_start,
            "end": item.end,
            "n_daily_observations": int(item.daily_observations),
        }
        rows.append(
            {
                **common,
                "direction_variant": "NORMAL",
                "case_status": "REUSABLE_AUTHORITATIVE_RESULT",
                "Return": item.Return,
                "Sharpe": item.Sharpe,
                "Signed_BE": item.Signed_BE,
                "MaxDD": item.MaxDD,
                "Total_Turnover_raw": item.Total_Turnover_raw,
                "metric_coverage": "FULL",
                "metric_source": "AUTHORITATIVE_NORMAL_MASTER",
                "source_path": rel(repo, master),
            }
        )
        if symbol in NEW:
            source = new_reverse(research, symbol) / "summary.json"
            rev = json.loads(source.read_text(encoding="utf-8-sig"))
            if (
                rev.get("status") != "COMPLETED"
                or rev.get("execution_variant") != "STRICT_REVERSE"
                or rev.get("strict_reverse_target_mismatch_count") != 0
            ):
                raise ValueError(f"invalid rerun: {source}")
            values = {
                "Return": rev["Return_fee0"],
                "Sharpe": rev["Sharpe"],
                "Signed_BE": rev["BE_bps"],
                "MaxDD": rev["MDD"],
                "Total_Turnover_raw": rev["Turnover_raw"],
                "metric_coverage": "FULL",
                "metric_source": "ACTUAL_FULL_WINDOW_RERUN_SUMMARY",
            }
        else:
            blocks = yearly[
                yearly.symbol.eq(symbol) & yearly.direction_variant.eq("STRICT_REVERSE")
            ]
            value, turnover, source = (
                float(blocks.Return.sum()),
                float(blocks.Turnover_raw.sum()),
                comparison,
            )
            values = {
                "Return": value,
                "Sharpe": pd.NA,
                "Signed_BE": value * 10_000 / turnover,
                "MaxDD": pd.NA,
                "Total_Turnover_raw": turnover,
                "metric_coverage": "EXACT_RETURN_BE_TURNOVER; FULL_WINDOW_SHARPE_MAXDD_UNAVAILABLE",
                "metric_source": "ACTUAL_SPLIT_RERUN_SUMMARIES_AGGREGATED",
            }
        rows.append(
            {
                **common,
                "direction_variant": "STRICT_REVERSE",
                "case_status": "REUSABLE_ACTUAL_RERUN",
                **values,
                "source_path": rel(repo, source),
            }
        )
    result = pd.DataFrame(rows).sort_values(["symbol", "direction_variant"]).reset_index(drop=True)
    if len(result) != 16 or not result.groupby("symbol").direction_variant.nunique().eq(2).all():
        raise ValueError("paired table is not 8/8")
    return result


def candidate_rows(paired: pd.DataFrame, yearly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for symbol in SYMBOLS:
        blocks = yearly[yearly.symbol.eq(symbol)].pivot_table(
            index="block", columns="direction_variant", values="Return"
        )
        better = bool((blocks.STRICT_REVERSE > blocks.NORMAL).all())
        full = paired[paired.symbol.eq(symbol)].set_index("direction_variant")
        rows.append(
            {
                "symbol": symbol,
                "strategy_id": STRATEGY,
                "timeframe": TIMEFRAME,
                "evidence_class": EVIDENCE,
                "NORMAL_Return": full.loc["NORMAL", "Return"],
                "STRICT_REVERSE_Return": full.loc["STRICT_REVERSE", "Return"],
                "strict_reverse_better_in_both_yearly_blocks": better,
                "preferred_historical_direction": "STRICT_REVERSE"
                if better
                else "NORMAL_OR_UNRESOLVED",
                "forward_candidate_reason": "HISTORICAL_REPLICATION_ONLY_NO_CLEAN_FORWARD_EVIDENCE",
                "forward_launch_authorized": False,
            }
        )
    return pd.DataFrame(rows)


def render(paired: pd.DataFrame, yearly: pd.DataFrame, path: Path) -> None:
    order = list(SYMBOLS)
    full = paired.pivot_table(index="symbol", columns="direction_variant", values="Return").reindex(
        order
    )
    annual = yearly.pivot_table(
        index="symbol", columns=["direction_variant", "block"], values="Return"
    ).reindex(order)
    x = np.arange(8)
    width = 0.36
    fig, axes = plt.subplots(2, 1, figsize=(13, 9), sharex=True)
    axes[0].bar(x - width / 2, full.NORMAL, width, label="NORMAL", color="#4C78A8")
    axes[0].bar(x + width / 2, full.STRICT_REVERSE, width, label="STRICT_REVERSE", color="#F58518")
    axes[0].axhline(0, color="black", lw=0.8)
    axes[0].set_ylabel("Full-window Return")
    axes[0].legend()
    for i, block in enumerate(("Y1", "Y2")):
        offset = (-1.5 + i) * width / 2
        axes[1].bar(
            x + offset,
            annual[("NORMAL", block)],
            width / 2,
            label=f"NORMAL {block}",
            color=("#72A0C1", "#33658A")[i],
        )
        axes[1].bar(
            x + offset + width,
            annual[("STRICT_REVERSE", block)],
            width / 2,
            label=f"STRICT_REVERSE {block}",
            color=("#FFB25B", "#E4572E")[i],
        )
    axes[1].axhline(0, color="black", lw=0.8)
    axes[1].set_ylabel("Yearly-block Return")
    axes[1].legend(ncol=2, fontsize=9)
    axes[1].set_xticks(x, order, rotation=30, ha="right")
    fig.suptitle("dynamic_breakout_short 1m — historical cross-symbol replication")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.png")
    fig.savefig(tmp, dpi=160, bbox_inches="tight")
    plt.close(fig)
    os.replace(tmp, path)


def build(repo: Path, research: Path, delivery: Path) -> dict[str, object]:
    normal = normal_master(repo)
    comparison_path = (
        repo
        / "outputs/deliverables/strategy_execution_reverse_review/reverse_validation/reverse_case_comparison.csv"
    )
    comparison = pd.read_csv(comparison_path)
    old = comparison[
        comparison.strategy_id.eq(STRATEGY)
        & comparison.timeframe.eq(TIMEFRAME)
        & comparison.symbol.isin(EXISTING)
    ].copy()
    if len(old) != 4 or old.symbol.nunique() != 4:
        raise ValueError(f"expected four existing split reruns, found {len(old)}")
    source_research = repo / "outputs/baseline_evaluation/dynamic_breakout_short_cross_symbol"
    yearly = yearly_rows(repo, source_research, old.set_index("symbol"))
    paired = paired_rows(repo, source_research, normal, yearly)
    candidates = candidate_rows(paired, yearly)
    for root in (research, delivery):
        atomic_csv(paired, root / "dynamic_breakout_short_cross_symbol.csv")
        atomic_csv(yearly, root / "yearly_robustness.csv")
        atomic_csv(candidates, root / "next_forward_symbol_candidates.csv")
        render(paired, yearly, root / "cross_symbol_normal_vs_strict_reverse.png")
        stale = root / "missing_reverse_reruns.csv"
        if stale.exists():
            stale.unlink()
        names = (
            "dynamic_breakout_short_cross_symbol.csv",
            "yearly_robustness.csv",
            "next_forward_symbol_candidates.csv",
            "cross_symbol_normal_vs_strict_reverse.png",
        )
        summary = {
            "status": "PASSED",
            "evidence_class": EVIDENCE,
            "strategy": STRATEGY,
            "timeframe": TIMEFRAME,
            "symbols_requested": 8,
            "normal_cases_reusable": 8,
            "strict_reverse_cases_reusable": 8,
            "complete_symbol_pairs": 8,
            "paired_rows": 16,
            "yearly_rows": 32,
            "forward_candidate_rows": 8,
            "new_full_window_reruns_reused": 4,
            "existing_split_reruns_reused": 4,
            "full_window_reverse_metrics_complete_cases": 4,
            "full_window_reverse_metrics_partial_cases": 4,
            "partial_metric_limitation": "SOL/XRP/DOGE/SUI full-window Sharpe and MaxDD are unavailable from actual split summaries and remain blank",
            "backtests_rerun_during_packaging": 0,
            "market_data_downloads": 0,
            "production_orders": 0,
            "forward_launches": 0,
            "forward_launch_authorized": False,
            "p0_touched": False,
            "p0_status_at_packaging": "BLOCKED_STOPPED_TASK_CONTEXT",
            "source_paths": {
                "normal_master": "outputs/deliverables/multi_symbol_classification/multi_symbol_classification_master.csv",
                "normal_review_root": "outputs/baseline_evaluation/tick_review_stageA_9symbols_preworkbook/matrix_cases",
                "new_full_window_reverse_root": "outputs/baseline_evaluation/dynamic_breakout_short_cross_symbol/reverse_cases",
                "existing_split_reverse_summary": "outputs/deliverables/strategy_execution_reverse_review/reverse_validation/reverse_case_comparison.csv",
                "existing_split_reverse_path_root": "outputs/baseline_evaluation/execution_method_and_reverse_review/reverse_validation/shards",
            },
            "artifact_sha256": {name: sha256(root / name) for name in names},
        }
        atomic_json(summary, root / "validation_summary.json")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--research-output", type=Path)
    parser.add_argument("--delivery-output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    research = (
        args.research_output
        or repo / "outputs/baseline_evaluation/dynamic_breakout_short_cross_symbol"
    ).resolve()
    delivery = (
        args.delivery_output or repo / "outputs/deliverables/dynamic_breakout_short_cross_symbol"
    ).resolve()
    print(json.dumps(build(repo, research, delivery), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
