#!/usr/bin/env python3
"""Build Phase 6B cost/episode stress evidence from persisted Phase 6A results."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import zipfile
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from results.trade_episode import build_de_risk_episodes
from scripts.internal.build_phase4a_baseline_evaluation import drawdown
from scripts.internal.build_phase4a_baseline_evaluation import period_label
from scripts.internal.build_phase4b_cost_episode_audit import COST_GRID
from scripts.internal.build_phase4b_cost_episode_audit import DURATION_BUCKETS
from scripts.internal.build_phase4b_cost_episode_audit import EPISODE_THRESHOLDS
from scripts.internal.build_phase4b_cost_episode_audit import completed_episode_metrics
from scripts.internal.build_phase4b_cost_episode_audit import cost_adjusted_increments
from scripts.internal.build_phase4b_cost_episode_audit import duration_bucket_rows
from scripts.internal.build_phase4b_cost_episode_audit import exact_be
from scripts.internal.build_phase4b_cost_episode_audit import remove_top_episodes
from scripts.internal.build_phase4b_cost_episode_audit import spearman_relationships
from scripts.internal.build_phase6a_expanded_screen import PERIODS
from scripts.internal.build_phase6a_expanded_screen import ROOT
from scripts.internal.build_phase6a_expanded_screen import TOL
from scripts.internal.build_phase6a_expanded_screen import atomic_csv
from scripts.internal.build_phase6a_expanded_screen import atomic_json
from scripts.internal.build_phase6a_expanded_screen import compare_snapshots
from scripts.internal.build_phase6a_expanded_screen import inventory
from scripts.internal.build_phase6a_expanded_screen import sha256
from scripts.internal.build_phase6a_expanded_screen import saved_phase5_drawdown


PHASE6A = ROOT / "outputs/baseline_evaluation/phase6a"
PHASE4B = ROOT / "outputs/baseline_evaluation/phase4b"
OUTPUT = ROOT / "outputs/baseline_evaluation/phase6b"
DELIVERABLES = ROOT / "outputs/deliverables"
LOPO_GRID = (0.0, 0.10, 0.20, 0.30, 0.50)
PHASE5_PHASES = {"PHASE5A", "PHASE5B", "PHASE5C", "PHASE5E", "PHASE5F"}


def phase6b_protected_snapshot(output: Path) -> dict[str, Any]:
    roots = [ROOT / "strategies", ROOT / "configs/semantic_contracts", PHASE6A]
    roots += [path for path in DELIVERABLES.iterdir() if path.exists() and path != output and path.name.startswith(("phase", "workbook_", "existing_"))]
    files: dict[str, dict[str, Any]] = {}
    for root in roots:
        candidates = [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())
        for path in candidates:
            if "phase6b" in path.parts or path.name.startswith("phase6b_") or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            files[relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for relative, metadata in sorted(files.items()):
        digest.update(f"{relative}\0{metadata['size']}\0{metadata['sha256']}\n".encode())
    return {
        "content_file_count": len(files), "content_digest": digest.hexdigest(), "files": files,
        "data_inventories": {
            "market_data": inventory(ROOT / "historical_data/market_data"),
            "feature_data": inventory(ROOT / "historical_data/feature_data"),
        },
    }


def finite(value: Any) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def canonical_drawdown(increments: np.ndarray, recovery_phase: str) -> float:
    if recovery_phase in PHASE5_PHASES:
        return saved_phase5_drawdown(np.asarray(increments, dtype=np.float64))
    return drawdown(np.asarray(increments, dtype=np.float64))


def canonical_episodes(strategy: str, row: pd.Series, coverage: pd.Series, timeseries: pd.DataFrame) -> pd.DataFrame:
    episode_path = str(coverage.episode_path)
    if episode_path and episode_path != "DERIVED_IN_MEMORY" and Path(episode_path).is_file():
        frame = pd.read_csv(episode_path)
        if "premium_mode" in frame:
            frame = frame[frame.premium_mode.astype(str).str.lower() == "included"].copy()
        return frame.reset_index(drop=True)
    episodes, _ = build_de_risk_episodes(
        event_time_ns=timeseries.event_time_ns,
        executed_position=timeseries.normal_direction,
        turnover_increment=timeseries.normal_turnover,
        gross_return_increment=timeseries.normal_total_return,
        strategy=strategy,
        symbol="BTCUSDT",
        granularity=str(row.canonical_timeframe),
        lag=str(row.realistic_lag),
        premium_mode="included",
    )
    return pd.DataFrame(episodes)


def readable_duration(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "NA"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def episode_return_metrics(episodes: pd.DataFrame) -> dict[str, float]:
    values = episodes.delta_gross_return.to_numpy(float)
    return {
        "episode_return_median": float(np.median(values)),
        "episode_return_p10": float(np.quantile(values, .10)),
        "episode_return_p25": float(np.quantile(values, .25)),
        "episode_return_p75": float(np.quantile(values, .75)),
        "episode_return_p90": float(np.quantile(values, .90)),
        "episode_return_positive_fraction": float(np.mean(values > TOL)),
    }


def primary_label(row: dict[str, Any]) -> tuple[str, str]:
    if int(row["episode_count"]) == 0:
        return "INSUFFICIENT_EPISODE_EVIDENCE", "no canonical completed episodes"
    if not bool(row["SURVIVES_0_10_BPS"]):
        return "COST_FRAGILE", "positive fee-zero baseline fails 0.10-bps total-cost stress"
    if row["episode_BE_median"] <= TOL or row["episode_BE_positive_fraction"] <= .5:
        return "EPISODE_FRAGILE", "median or majority completed-episode BE is non-positive"
    survives_winner_removal = row["Return_without_top5pct"] > TOL and row["BE_without_top5pct"] > TOL
    strong = bool(row["LOPO_0_10"]) and survives_winner_removal
    if strong:
        return "ECONOMICALLY_STRONG", "survives 0.10 bps, LOPO, episode breadth, and top-5% winner removal"
    if bool(row["winner_concentrated"]) and survives_winner_removal:
        return "WINNER_CONCENTRATED_BUT_SURVIVES", "winner-concentrated flag but Return/BE remain positive without top 5%"
    if not bool(row["LOPO_0_10"]):
        return "TEMPORALLY_FRAGILE", "0.10-bps leave-one-period-out Return is not robust"
    return "BROAD_BUT_LOW_MARGIN", "survives 0.10 bps but severe winner-removal evidence fails"


def render_aggregate_figures(output: Path, master: pd.DataFrame, stress: pd.DataFrame, incremental: pd.DataFrame) -> None:
    root = output / "figures"
    root.mkdir(parents=True, exist_ok=True)
    provenance_colors = dict(zip(
        ["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED", "P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"],
        ["#2166ac", "#4393c3", "#92c5de", "#f4a582", "#b2182b"], strict=True,
    ))

    def scatter(x: str, y: str, xlabel: str, ylabel: str, filename: str) -> None:
        fig, ax = plt.subplots(figsize=(9, 6))
        for tier, child in master.groupby("provenance_tier"):
            ax.scatter(child[x], child[y], label=tier, color=provenance_colors[tier], alpha=.78)
        ax.axhline(0, color="0.45", lw=.8)
        ax.axvline(0, color="0.45", lw=.8)
        ax.set(xlabel=xlabel, ylabel=ylabel)
        ax.legend(title="Provenance", fontsize=8)
        ax.grid(alpha=.2)
        fig.tight_layout(); fig.savefig(root / filename, dpi=160); plt.close(fig)

    scatter("baseline_BE", "return_0_10", "Global signed BE (bps)", "Return at 0.10 bps", "01_global_be_vs_010_return.png")
    scatter("baseline_BE", "episode_BE_median", "Global signed BE (bps)", "Median episode BE (bps)", "02_global_vs_episode_be.png")
    scatter("baseline_Return", "Return_without_top5pct", "Baseline Return (1x)", "Return without top 5% winners", "03_baseline_vs_without_top5pct.png")
    scatter("baseline_BE", "episode_BE_positive_fraction", "Global signed BE (bps)", "Fraction episodes with BE > 0", "05_episode_breadth_vs_global_be.png")

    costs = [0.0, .10, .20, .30, .50, 1.0]
    pivot = stress[stress.cost_bps.isin(costs)].pivot(index="representative_strategy_id", columns="cost_bps", values="survives_cost")
    pivot = pivot.reindex(master.sort_values(["phase6b_economic_label", "representative_strategy_id"]).representative_strategy_id)
    fig, ax = plt.subplots(figsize=(10, max(7, len(pivot) * .28)))
    image = ax.imshow(pivot.to_numpy(float), aspect="auto", vmin=0, vmax=1, cmap="RdYlGn")
    ax.set_xticks(range(len(costs)), [f"{value:.2f}" for value in costs])
    ax.set_yticks(range(len(pivot)), pivot.index, fontsize=7)
    ax.set(xlabel="Hypothetical total cost (bps)", title="Cost Survival")
    fig.colorbar(image, ax=ax, label="Final Return > 0")
    fig.tight_layout(); fig.savefig(root / "04_cost_survival_heatmap.png", dpi=160); plt.close(fig)

    labels = pd.crosstab(master.provenance_tier, master.phase6b_economic_label)
    fig, ax = plt.subplots(figsize=(11, 5)); labels.plot.bar(stacked=True, ax=ax)
    ax.set(xlabel="Provenance", ylabel="Semantic groups"); ax.legend(title="Phase 6B label", fontsize=7)
    fig.tight_layout(); fig.savefig(root / "06_labels_by_provenance.png", dpi=160); plt.close(fig)

    if not incremental.empty:
        values = incremental.set_index("representative_strategy_id")[["return_0_10", "return_0_20", "return_0_30", "return_0_50"]]
        fig, ax = plt.subplots(figsize=(10, max(6, len(values) * .3)))
        image = ax.imshow(values.to_numpy(float), aspect="auto", cmap="RdYlGn")
        ax.set_xticks(range(4), ["0.10", "0.20", "0.30", "0.50"])
        ax.set_yticks(range(len(values)), values.index, fontsize=7)
        ax.set(xlabel="Hypothetical total cost (bps)", title="Phase 5 Incremental Candidate Return")
        fig.colorbar(image, ax=ax, label="Return (1x)")
        fig.tight_layout(); fig.savefig(root / "07_phase5_incremental_cost_survival.png", dpi=160); plt.close(fig)


def render_candidate_figure(output: Path, row: pd.Series, costs: pd.DataFrame, episodes: pd.DataFrame) -> None:
    destination = output / "figures" / "phase6c_candidates"
    destination.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    axes[0, 0].plot(costs.cost_bps, costs.net_Return, marker="o")
    axes[0, 0].axhline(0, color="black", lw=.8); axes[0, 0].axvline(row.baseline_BE, color="crimson", ls="--")
    axes[0, 0].set(xlabel="Total cost (bps)", ylabel="Final Return (1x)", title="Cost stress")
    be = episodes.break_even_bps.to_numpy(float)
    axes[0, 1].hist(be, bins=60, alpha=.5); axes[0, 1].set_xscale("symlog", linthresh=1)
    axes[0, 1].set(xlabel="Episode BE (bps)", ylabel="Count", title="Episode BE distribution")
    positive = np.sort(np.clip(episodes.delta_gross_return.to_numpy(float), 0, None))[::-1]
    positive = positive[positive > 0]
    cumulative = np.cumsum(positive) / positive.sum() if len(positive) else np.array([])
    if len(cumulative): axes[1, 0].plot(np.arange(1, len(cumulative) + 1) / len(cumulative), cumulative)
    axes[1, 0].plot([0, 1], [0, 1], ls=":", color="gray")
    axes[1, 0].set(xlabel="Fraction positive episodes", ylabel="Cumulative positive Return share", title="Winner concentration")
    duration = (pd.to_datetime(episodes.completion_timestamp, utc=True) - pd.to_datetime(episodes.start_timestamp, utc=True)).dt.total_seconds() / 3600
    axes[1, 1].hist(duration, bins=60, alpha=.55); axes[1, 1].set_xscale("symlog", linthresh=1 / 60)
    axes[1, 1].set(xlabel="Holding duration (hours)", ylabel="Count", title="Holding duration")
    fig.suptitle(f"{row.representative_strategy_id} — {row.phase6b_economic_label} — {row.provenance_tier}")
    fig.tight_layout(); fig.savefig(destination / f"{row.representative_strategy_id}_diagnostic.png", dpi=150); plt.close(fig)


def robust_summary(frame: pd.DataFrame, dimension: str) -> pd.DataFrame:
    rows = []
    for value, child in frame.groupby(dimension):
        rows.append({
            dimension: value, "groups_analyzed": len(child),
            "survive_0_10": int(child.SURVIVES_0_10_BPS.sum()), "survive_0_20": int(child.SURVIVES_0_20_BPS.sum()),
            "survive_0_30": int(child.SURVIVES_0_30_BPS.sum()), "survive_0_50": int(child.SURVIVES_0_50_BPS.sum()),
            "median_episode_BE_positive": int(child.MEDIAN_EPISODE_BE_POSITIVE.sum()),
            "majority_episode_BE_positive": int(child.MAJORITY_EPISODES_BE_POSITIVE.sum()),
            "return_BE_positive_without_top5pct": int(child.RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT.sum()),
            "ECONOMICALLY_STRONG_groups": int((child.phase6b_economic_label == "ECONOMICALLY_STRONG").sum()),
        })
    return pd.DataFrame(rows)


def package(output: Path) -> tuple[Path, str, int, int]:
    target = DELIVERABLES / "phase6b_cost_episode_review.zip"
    temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase6b_delivery.json":
                archive.write(path, Path("phase6b_cost_episode_review") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip(); members = len(archive.infolist())
    if bad:
        raise RuntimeError(f"ZIP integrity failure: {bad}")
    return target, hashlib.sha256(target.read_bytes()).hexdigest(), members, target.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase6a-root", type=Path, default=PHASE6A)
    parser.add_argument("--phase4b-root", type=Path, default=PHASE4B)
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    args = parser.parse_args(); args.output_root.mkdir(parents=True, exist_ok=True)

    before = phase6b_protected_snapshot(args.output_root); atomic_json(args.output_root / "phase6b_protected_hashes_before.json", before)
    candidates = pd.read_csv(args.phase6a_root / "phase6a_phase6b_candidates.csv")
    master6a = pd.read_csv(args.phase6a_root / "phase6a_strategy_master.csv")
    universe = pd.read_csv(args.phase6a_root / "phase6a_strategy_universe.csv")
    coverage = pd.read_csv(args.phase6a_root / "phase6a_baseline_result_coverage.csv").set_index("strategy_id")
    semantic = pd.read_csv(args.phase6a_root / "phase6a_semantic_group_summary.csv").set_index("equivalence_group_id")
    representatives = master6a[master6a.strategy_id == master6a.group_representative].set_index("strategy_id")
    ids = list(candidates.strategy_id)
    if len(ids) != 28 or len(set(ids)) != 28:
        raise ValueError(f"Phase 6B scope must be 28 unique groups, got {len(ids)}/{len(set(ids))}")
    if set(ids) - set(representatives.index):
        raise ValueError("Phase 6B scope contains non-representative identities")

    phase4b_scope_path = args.phase4b_root / "phase4b_strategy_scope.csv"
    phase4b_ids = set(pd.read_csv(phase4b_scope_path).representative_strategy_id) if phase4b_scope_path.is_file() else set()
    phase4b_stress = pd.read_csv(args.phase4b_root / "phase4b_cost_stress.csv") if (args.phase4b_root / "phase4b_cost_stress.csv").is_file() else pd.DataFrame()
    phase4b_margin = pd.read_csv(args.phase4b_root / "phase4b_episode_cost_margin.csv").set_index("strategy_id") if (args.phase4b_root / "phase4b_episode_cost_margin.csv").is_file() else pd.DataFrame()
    phase4b_concentration = pd.read_csv(args.phase4b_root / "phase4b_episode_concentration.csv").set_index("strategy_id") if (args.phase4b_root / "phase4b_episode_concentration.csv").is_file() else pd.DataFrame()
    phase4b_duration = pd.read_csv(args.phase4b_root / "phase4b_holding_duration_analysis.csv") if (args.phase4b_root / "phase4b_holding_duration_analysis.csv").is_file() else pd.DataFrame()

    scope_rows: list[dict[str, Any]] = []; stress_rows: list[dict[str, Any]] = []; period_rows: list[dict[str, Any]] = []
    episode_capacity_rows: list[dict[str, Any]] = []; winner_rows: list[dict[str, Any]] = []; duration_rows: list[dict[str, Any]] = []
    relationship_rows: list[dict[str, Any]] = []; master_rows: list[dict[str, Any]] = []; overlap_rows: list[dict[str, Any]] = []
    episode_frames: dict[str, pd.DataFrame] = {}; stress_frames: dict[str, pd.DataFrame] = {}
    max_residuals = {key: 0.0 for key in ("cost0_return", "cost0_mdd", "turnover", "global_BE", "episode_count", "be_crossing", "period_cost", "episode_removal")}
    phase4b_residuals = {key: 0.0 for key in ("cost_return", "cost_mdd", "episode_fraction", "winner", "duration")}
    accounting = {key: 0.0 for key in ("gross_price_return", "slippage_return", "trading_return", "funding_return", "total_return")}

    for number, strategy in enumerate(ids, 1):
        row = representatives.loc[strategy]; cover = coverage.loc[strategy]
        ts_path = Path(str(cover.timeseries_path))
        columns = ["event_time_ns", "normal_direction", "normal_total_return", "normal_turnover"]
        available = pd.read_parquet(ts_path).columns
        component_columns = [f"normal_{key}" for key in accounting if f"normal_{key}" in available and f"normal_{key}" not in columns]
        timeseries = pd.read_parquet(ts_path, columns=columns + component_columns)
        timeseries["period"] = period_label(timeseries.event_time_ns)
        for key in accounting:
            column = f"normal_{key}"
            if column in timeseries: accounting[key] += float(timeseries[column].sum())
        gross = timeseries.normal_total_return.to_numpy(float); turnover = timeseries.normal_turnover.to_numpy(float)
        episodes = canonical_episodes(strategy, row, cover, timeseries)
        if episodes.empty:
            raise ValueError(f"{strategy}: candidate has no canonical completed episodes")
        episode_frames[strategy] = episodes
        margin, concentration = completed_episode_metrics(episodes)
        duration_buckets, duration_summary = duration_bucket_rows(strategy, episodes)
        start = pd.to_datetime(episodes.start_timestamp, utc=True); end = pd.to_datetime(episodes.completion_timestamp, utc=True)
        duration_values = (end - start).dt.total_seconds().to_numpy(float)
        relationship_rows.extend({**item, "semantic_group_id": row.equivalence_group_id} for item in spearman_relationships(strategy, episodes, duration_values))
        duration_rows.append({
            "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy, "duration_bucket": "ALL",
            "episode_count": len(episodes), **duration_summary,
            "duration_p99": float(np.quantile(duration_values, .99)), "duration_max": float(np.max(duration_values)),
            "duration_median_readable": readable_duration(duration_summary["duration_median_seconds"]),
            "duration_p95_readable": readable_duration(duration_summary["duration_p95"]),
        })
        duration_rows.extend({**item, "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy} for item in duration_buckets)
        episode_capacity_rows.append({
            "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy,
            **margin, **episode_return_metrics(episodes),
            "MEDIAN_EPISODE_BE_POSITIVE": margin["BE_median"] > TOL,
            "MAJORITY_EPISODES_BE_POSITIVE": margin["fraction_BE_gt_0_00"] > .5,
            "MAJORITY_EPISODES_SURVIVE_0_10": margin["fraction_BE_gt_0_10"] > .5,
            "MAJORITY_EPISODES_SURVIVE_0_20": margin["fraction_BE_gt_0_20"] > .5,
            "MAJORITY_EPISODES_SURVIVE_0_30": margin["fraction_BE_gt_0_30"] > .5,
        })
        winner_concentrated = bool(concentration["top_best5pct_positive_return_share"] >= .50 or concentration["return_without_best5pct"] <= 0)
        winner_reason = ";".join(filter(None, (
            "TOP5PCT_POSITIVE_SHARE_GE_50PCT" if concentration["top_best5pct_positive_return_share"] >= .50 else "",
            "RETURN_WITHOUT_TOP5PCT_NONPOSITIVE" if concentration["return_without_best5pct"] <= 0 else "",
        )))
        winner_rows.append({
            "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy, **concentration,
            "WINNER_CONCENTRATED": winner_concentrated, "winner_concentration_reason": winner_reason,
            "RETURN_POSITIVE_WITHOUT_TOP5PCT": concentration["return_without_best5pct"] > TOL,
            "BE_POSITIVE_WITHOUT_TOP5PCT": concentration["BE_without_best5pct"] > TOL,
            "RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT": concentration["return_without_best5pct"] > TOL and concentration["BE_without_best5pct"] > TOL,
        })

        total_return = float(gross.sum()); total_turnover = float(turnover.sum()); global_be = exact_be(total_return, total_turnover)
        strategy_stress = []
        for cost in COST_GRID:
            increments = cost_adjusted_increments(gross, turnover, cost)
            period = pd.DataFrame({"period": timeseries.period, "net": increments, "turnover": turnover}).groupby("period", sort=False).agg(net_Return=("net", "sum"), Turnover=("turnover", "sum"))
            net = float(increments.sum()); leave = net - period.net_Return.to_numpy(float)
            stress_row = {
                "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy,
                "provenance_tier": row.semantic_provenance_tier, "cost_bps": cost,
                "cost_interpretation": "HYPOTHETICAL_TOTAL_TRANSACTION_COST",
                "net_Return": net, "MDD": canonical_drawdown(increments, str(row.coverage_recovery_phase)),
                "Turnover": total_turnover, "positive_period_count": int((period.net_Return > TOL).sum()),
                "negative_period_count": int((period.net_Return < -TOL).sum()),
                "LOPO_positive_at_cost": bool(np.all(leave > TOL)), "minimum_LOPO_Return_at_cost": float(np.min(leave)),
                "survives_cost": net > TOL,
            }
            stress_rows.append(stress_row); strategy_stress.append(stress_row)
            for period_name in PERIODS:
                child = period.loc[period_name] if period_name in period.index else None
                if child is not None:
                    period_rows.append({
                        "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy,
                        "cost_bps": cost, "period": period_name, "net_Return": float(child.net_Return),
                        "Turnover": float(child.Turnover), "positive": float(child.net_Return) > TOL,
                        "cost_margin_bps": exact_be(float(child.net_Return), float(child.Turnover)) + cost if child.Turnover > 0 else math.nan,
                    })
            max_residuals["period_cost"] = max(max_residuals["period_cost"], abs(float(period.net_Return.sum()) - net))
        stress_frame = pd.DataFrame(strategy_stress); stress_frames[strategy] = stress_frame
        at = {float(item.cost_bps): item for item in stress_frame.itertuples()}
        max_residuals["cost0_return"] = max(max_residuals["cost0_return"], abs(at[0.0].net_Return - float(row.Return)))
        max_residuals["cost0_mdd"] = max(max_residuals["cost0_mdd"], abs(at[0.0].MDD - float(row.MDD)))
        max_residuals["turnover"] = max(max_residuals["turnover"], abs(total_turnover - float(row.Turnover)))
        max_residuals["global_BE"] = max(max_residuals["global_BE"], abs(global_be - float(row.BE)))
        max_residuals["episode_count"] = max(max_residuals["episode_count"], abs(len(episodes) - int(row.Episode_Count)))
        max_residuals["be_crossing"] = max(max_residuals["be_crossing"], abs(total_return - total_turnover * global_be / 10_000.0))
        for key in ("best1", "best5", "best1pct", "best5pct"):
            max_residuals["episode_removal"] = max(max_residuals["episode_removal"], abs(
                concentration[f"return_without_{key}"] - concentration[f"turnover_without_{key}"] * concentration[f"BE_without_{key}"] / 10_000.0
            ))

        sem = semantic.loc[row.equivalence_group_id]
        scope_rows.append({
            "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy,
            "member_strategy_ids": sem.member_ids, "phase6a_quality_tier": row.baseline_quality_tier,
            "semantic_provenance": row.semantic_provenance, "provenance_tier": row.semantic_provenance_tier,
            "coverage_recovery_phase": row.coverage_recovery_phase, "canonical_timeframe": row.canonical_timeframe,
            "canonical_realistic_lag": row.realistic_lag,
        })
        master_rows.append({
            "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy,
            "Phase6A_quality_tier": row.baseline_quality_tier, "semantic_provenance": row.semantic_provenance,
            "provenance_tier": row.semantic_provenance_tier, "coverage_recovery_phase": row.coverage_recovery_phase,
            "contracts_applied": row.contracts_applied, "baseline_Return": float(row.Return), "baseline_BE": float(row.BE),
            "baseline_MDD": float(row.MDD), "baseline_Turnover": float(row.Turnover), "episode_count": len(episodes),
            **{f"return_{cost:.2f}".replace(".", "_"): at[cost].net_Return for cost in (.05, .10, .20, .30, .50, 1.0)},
            **{f"positive_periods_{cost:.2f}".replace(".", "_"): at[cost].positive_period_count for cost in (0.0, .10, .20, .30, .50)},
            **{f"LOPO_{cost:.2f}".replace(".", "_"): at[cost].LOPO_positive_at_cost for cost in LOPO_GRID},
            **{f"minimum_LOPO_Return_{cost:.2f}".replace(".", "_"): at[cost].minimum_LOPO_Return_at_cost for cost in LOPO_GRID},
            "episode_BE_median": margin["BE_median"], "episode_BE_p25": margin["BE_p25"],
            "episode_BE_positive_fraction": margin["fraction_BE_gt_0_00"],
            "episode_BE_gt_0_10_fraction": margin["fraction_BE_gt_0_10"], "episode_BE_gt_0_20_fraction": margin["fraction_BE_gt_0_20"],
            "episode_BE_gt_0_30_fraction": margin["fraction_BE_gt_0_30"], "episode_BE_gt_0_50_fraction": margin["fraction_BE_gt_0_50"],
            "top1pct_positive_return_share": concentration["top_best1pct_positive_return_share"],
            "top5pct_positive_return_share": concentration["top_best5pct_positive_return_share"],
            "Return_without_top1pct": concentration["return_without_best1pct"], "BE_without_top1pct": concentration["BE_without_best1pct"],
            "Return_without_top5pct": concentration["return_without_best5pct"], "BE_without_top5pct": concentration["BE_without_best5pct"],
            "holding_duration_median": duration_summary["duration_median_seconds"], "holding_duration_p95": duration_summary["duration_p95"],
            "winner_concentrated": winner_concentrated,
            "MEDIAN_EPISODE_BE_POSITIVE": margin["BE_median"] > TOL,
            "MAJORITY_EPISODES_BE_POSITIVE": margin["fraction_BE_gt_0_00"] > .5,
            "MAJORITY_EPISODES_SURVIVE_0_10": margin["fraction_BE_gt_0_10"] > .5,
            "MAJORITY_EPISODES_SURVIVE_0_20": margin["fraction_BE_gt_0_20"] > .5,
            "MAJORITY_EPISODES_SURVIVE_0_30": margin["fraction_BE_gt_0_30"] > .5,
            "RETURN_POSITIVE_WITHOUT_TOP5PCT": concentration["return_without_best5pct"] > TOL,
            "BE_POSITIVE_WITHOUT_TOP5PCT": concentration["BE_without_best5pct"] > TOL,
            "RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT": concentration["return_without_best5pct"] > TOL and concentration["BE_without_best5pct"] > TOL,
            **{f"SURVIVES_{cost:.2f}_BPS".replace(".", "_"): at[cost].survives_cost for cost in (.05, .10, .20, .30, .50, 1.0)},
            "LOW_EPISODE_COUNT_WARNING": "RAW_COUNT_REPORTED_NO_AUTHORIZED_THRESHOLD" if len(episodes) < 30 else "NONE",
        })

        if strategy in phase4b_ids:
            current_stress = stress_frame.set_index("cost_bps")
            old_stress = phase4b_stress[phase4b_stress.representative_strategy_id == strategy].set_index("cost_bps")
            common = current_stress.index.intersection(old_stress.index)
            cost_return_residual = float((current_stress.loc[common, "net_Return"] - old_stress.loc[common, "net_return"]).abs().max())
            cost_mdd_residual = float((current_stress.loc[common, "MDD"] - old_stress.loc[common, "MDD"]).abs().max())
            old_margin = phase4b_margin.loc[strategy]; old_conc = phase4b_concentration.loc[strategy]
            episode_fraction_residual = abs(margin["fraction_BE_gt_0_00"] - float(old_margin.fraction_BE_gt_0_00))
            winner_residual = abs(concentration["top_best5pct_positive_return_share"] - float(old_conc.top_best5pct_positive_return_share))
            old_duration = phase4b_duration[(phase4b_duration.strategy_id == strategy)].iloc[0]
            duration_residual = abs(duration_summary["duration_median_seconds"] - float(old_duration.duration_median_seconds))
            phase4b_residuals["cost_return"] = max(phase4b_residuals["cost_return"], cost_return_residual)
            phase4b_residuals["cost_mdd"] = max(phase4b_residuals["cost_mdd"], cost_mdd_residual)
            phase4b_residuals["episode_fraction"] = max(phase4b_residuals["episode_fraction"], episode_fraction_residual)
            phase4b_residuals["winner"] = max(phase4b_residuals["winner"], winner_residual)
            phase4b_residuals["duration"] = max(phase4b_residuals["duration"], duration_residual)
            overlap_rows.append({
                "semantic_group_id": row.equivalence_group_id, "representative_strategy_id": strategy,
                "baseline_Return_residual": abs(float(row.Return) - at[0.0].net_Return),
                "BE_residual": abs(float(row.BE) - global_be), "MDD_residual": abs(float(row.MDD) - at[0.0].MDD),
                "episode_count_residual": abs(int(row.Episode_Count) - len(episodes)),
                "cost_stress_Return_max_residual": cost_return_residual, "cost_stress_MDD_max_residual": cost_mdd_residual,
                "episode_BE_fraction_residual": episode_fraction_residual, "winner_concentration_residual": winner_residual,
                "holding_duration_median_residual_seconds": duration_residual, "reconciliation_status": "PASSED",
            })
        print(f"PHASE6B {number}/28 {strategy}", flush=True)

    scope = pd.DataFrame(scope_rows); stress = pd.DataFrame(stress_rows); periods = pd.DataFrame(period_rows)
    episode_capacity = pd.DataFrame(episode_capacity_rows); winners = pd.DataFrame(winner_rows); durations = pd.DataFrame(duration_rows)
    relationships = pd.DataFrame(relationship_rows); master = pd.DataFrame(master_rows)
    for index, item in master.iterrows():
        label, reason = primary_label(item.to_dict()); master.loc[index, "phase6b_economic_label"] = label; master.loc[index, "phase6b_reason_trace"] = reason
    cost_columns = ["SURVIVES_1_00_BPS", "SURVIVES_0_50_BPS", "SURVIVES_0_30_BPS", "SURVIVES_0_20_BPS", "SURVIVES_0_10_BPS"]
    master["followup_priority"] = list(zip(
        *[~master[column].astype(bool) for column in cost_columns],
        ~master.LOPO_0_10.astype(bool), ~master.MEDIAN_EPISODE_BE_POSITIVE.astype(bool),
        ~master.MAJORITY_EPISODES_BE_POSITIVE.astype(bool), ~master.RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT.astype(bool),
        -master.baseline_BE, master.baseline_MDD, master.baseline_Turnover, master.representative_strategy_id,
    ))
    master = master.sort_values("followup_priority").reset_index(drop=True)
    strict = master[master.phase6b_economic_label == "ECONOMICALLY_STRONG"].copy()
    conditional = master[(master.SURVIVES_0_10_BPS) & (master.LOPO_0_10) & (master.baseline_BE > TOL) & ~master.index.isin(strict.index)].copy()
    conditional["phase6c_candidate_class"] = "CONDITIONAL_REPLICATION_CANDIDATE"
    strict["phase6c_candidate_class"] = "ECONOMICALLY_STRONG"
    phase6c = pd.concat([strict, conditional], ignore_index=True)
    phase6c["why_cross_symbol_replication"] = np.where(
        phase6c.phase6c_candidate_class == "ECONOMICALLY_STRONG",
        "strict BTC cost/LOPO/episode/winner-removal evidence survived",
        "worth cross-symbol falsification despite explicit episode/winner warning",
    )
    for item in phase6c.itertuples(): render_candidate_figure(args.output_root, pd.Series(item._asdict()), stress_frames[item.representative_strategy_id], episode_frames[item.representative_strategy_id])

    high = strict[strict.provenance_tier.isin(["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED"])]
    modelled = strict[strict.provenance_tier.isin(["P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"])]
    fragile = master[~master[["SURVIVES_0_10_BPS", "SURVIVES_0_20_BPS", "SURVIVES_0_30_BPS", "SURVIVES_0_50_BPS"]].all(axis=1)].copy()
    fragile["first_failing_stress_bps"] = fragile.apply(lambda item: next(cost for cost in (.10, .20, .30, .50) if not item[f"SURVIVES_{cost:.2f}_BPS".replace(".", "_")]), axis=1)
    episode_fragile = master[(master.episode_BE_median <= TOL) | (master.episode_BE_positive_fraction <= .5) | ~master.RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT]
    incremental = master[master.coverage_recovery_phase.isin(PHASE5_PHASES)].copy()
    provenance = robust_summary(master, "provenance_tier")
    coverage_summary = robust_summary(master, "coverage_recovery_phase")
    render_aggregate_figures(args.output_root, master, stress, incremental)

    scope_overlap = pd.DataFrame(overlap_rows)
    accounting_audit = {
        "canonical_equation": "R_fee0_funding_included = gross_price_return + funding_return",
        "cost_equation": "R_net(c) = R_fee0_funding_included - Turnover_raw * c / 10000",
        "break_even_equation": "BE_bps = R_fee0_funding_included * 10000 / Turnover_raw",
        "canonical_transaction_fee_bps": 0.0, "canonical_slippage_bps": 0.0,
        "stress_interpretation": "HYPOTHETICAL_TOTAL_TRANSACTION_COST_NOT_INCREMENTAL_COST",
        "execution_method": "chronological analytical overlay on saved canonical timeseries",
        "component_sums_across_scope": accounting,
    }
    atomic_json(args.output_root / "phase6b_cost_accounting_audit.json", accounting_audit)
    atomic_csv(args.output_root / "phase6b_scope_manifest.csv", scope)
    atomic_csv(args.output_root / "phase6b_phase4b_overlap.csv", scope_overlap)
    atomic_csv(args.output_root / "phase6b_cost_episode_master.csv", master)
    atomic_csv(args.output_root / "phase6b_cost_stress.csv", stress)
    atomic_csv(args.output_root / "phase6b_period_cost_robustness.csv", periods)
    atomic_csv(args.output_root / "phase6b_episode_cost_capacity.csv", episode_capacity)
    atomic_csv(args.output_root / "phase6b_winner_concentration.csv", winners)
    atomic_csv(args.output_root / "phase6b_holding_duration.csv", durations)
    atomic_csv(args.output_root / "phase6b_episode_relationships.csv", relationships)
    atomic_csv(args.output_root / "phase6b_provenance_robustness.csv", provenance)
    atomic_csv(args.output_root / "phase6b_coverage_phase_robustness.csv", coverage_summary)
    atomic_csv(args.output_root / "phase6b_high_certainty_survivors.csv", high)
    atomic_csv(args.output_root / "phase6b_modelled_survivors.csv", modelled)
    atomic_csv(args.output_root / "phase6b_cost_fragile.csv", fragile)
    atomic_csv(args.output_root / "phase6b_episode_fragile.csv", episode_fragile)
    atomic_csv(args.output_root / "phase6b_phase5_incremental_candidates.csv", incremental)
    atomic_csv(args.output_root / "phase6b_phase6c_candidates.csv", phase6c)

    cards = {
        "Candidates analyzed": len(master), "High certainty": int(master.provenance_tier.isin(["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED"]).sum()),
        "Modelled": int(master.provenance_tier.isin(["P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"]).sum()),
        "Survive 0.10 bps": int(master.SURVIVES_0_10_BPS.sum()), "Survive 0.20 bps": int(master.SURVIVES_0_20_BPS.sum()),
        "Survive 0.30 bps": int(master.SURVIVES_0_30_BPS.sum()), "Survive 0.50 bps": int(master.SURVIVES_0_50_BPS.sum()),
        "Median episode BE > 0": int(master.MEDIAN_EPISODE_BE_POSITIVE.sum()),
        "Majority episodes BE > 0": int(master.MAJORITY_EPISODES_BE_POSITIVE.sum()),
        "Return/BE survive top 5% removal": int(master.RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT.sum()),
        "ECONOMICALLY_STRONG": len(strict),
    }
    cards_html = "".join(f"<div class='card'><b>{html.escape(key)}</b><span>{value}</span></div>" for key, value in cards.items())
    columns = ["representative_strategy_id", "Phase6A_quality_tier", "provenance_tier", "coverage_recovery_phase", "baseline_Return", "baseline_BE", "return_0_10", "episode_BE_median", "episode_BE_positive_fraction", "Return_without_top5pct", "BE_without_top5pct", "phase6b_economic_label", "phase6b_reason_trace"]
    table = master[columns].to_html(index=False, border=0, float_format=lambda value: f"{value:.6g}")
    document = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 6B Cost and Episode Review</title><style>body{{font:14px system-ui;margin:28px}}.cards{{display:flex;gap:8px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:8px;padding:10px;display:flex;gap:10px}}.card span{{font-size:20px}}table{{border-collapse:collapse;width:100%;font-size:11px}}th,td{{padding:5px;border-bottom:1px solid #ddd;text-align:right}}th:first-child,td:first-child{{text-align:left}}.note{{background:#fff8dc;padding:12px;border-left:4px solid #d4a72c}}</style></head><body><h1>Phase 6B — Cost Capacity, Episode Breadth, Winner Concentration</h1><p class='note'>28 frozen semantic groups. ORIGINAL/NORMAL, Premium Included, canonical timeframe and realistic lag. Costs are hypothetical total transaction-cost stress. No optimization or cross-symbol run.</p><div class='cards'>{cards_html}</div><h2>All candidates</h2>{table}</body></html>"""
    temp = args.output_root / "phase6b_cost_episode_review.html.tmp"; temp.write_text(document, encoding="utf-8"); os.replace(temp, args.output_root / "phase6b_cost_episode_review.html")

    after = phase6b_protected_snapshot(args.output_root); atomic_json(args.output_root / "phase6b_protected_hashes_after.json", after)
    protected_changes, protected_paths_changed = compare_snapshots(before, after)
    x435 = master[master.representative_strategy_id == "xlsx_s2_0435"].iloc[0]
    label_counts = master.phase6b_economic_label.value_counts()
    summary = {
        "status": "PASSED", "semantic_groups_analyzed": len(master),
        "high_certainty_groups": int(master.provenance_tier.isin(["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED"]).sum()),
        "modelled_groups": int(master.provenance_tier.isin(["P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"]).sum()),
        "cost_survival": {f"{cost:.2f}": int(master[f"SURVIVES_{cost:.2f}_BPS".replace(".", "_")].sum()) for cost in (.10, .20, .30, .50, 1.0)},
        "episode_breadth": {
            "median_BE_positive": int(master.MEDIAN_EPISODE_BE_POSITIVE.sum()),
            "majority_BE_positive": int(master.MAJORITY_EPISODES_BE_POSITIVE.sum()),
            "majority_survive_0_10": int(master.MAJORITY_EPISODES_SURVIVE_0_10.sum()),
            "majority_survive_0_20": int(master.MAJORITY_EPISODES_SURVIVE_0_20.sum()),
            "majority_survive_0_30": int(master.MAJORITY_EPISODES_SURVIVE_0_30.sum()),
        },
        "winner_concentrated": int(master.winner_concentrated.sum()),
        "return_BE_survive_top5pct_removal": int(master.RETURN_AND_BE_POSITIVE_WITHOUT_TOP5PCT.sum()),
        "LOPO_robust_counts": {f"{cost:.2f}": int(master[f"LOPO_{cost:.2f}".replace(".", "_")].sum()) for cost in LOPO_GRID},
        "economic_label_counts": {label: int(label_counts.get(label, 0)) for label in (
            "ECONOMICALLY_STRONG", "COST_FRAGILE", "EPISODE_FRAGILE", "WINNER_CONCENTRATED_BUT_SURVIVES", "TEMPORALLY_FRAGILE", "BROAD_BUT_LOW_MARGIN", "INSUFFICIENT_EPISODE_EVIDENCE"
        )},
        "phase4b_overlap_groups": len(scope_overlap), "phase4b_max_residuals": phase4b_residuals,
        "phase6a_max_residuals": max_residuals,
        "xlsx_s2_0435": {key: finite(x435[key]) for key in ("baseline_Return", "baseline_BE", "baseline_MDD", "episode_count", "return_0_10", "return_0_20")},
        "phase6c_candidate_count": len(phase6c), "strict_phase6c_candidates": len(strict), "conditional_phase6c_candidates": len(conditional),
        "new_strategy_backtests": 0, "new_parameter_optimization_runs": 0, "new_semantic_contracts": 0,
        "new_strategy_registrations": 0, "new_non_BTC_backtests": 0, "phase5g_started": False, "phase6c_started": False,
        "protected_artifact_changes": protected_changes, "protected_change_paths": protected_paths_changed,
    }
    turnover_tolerance = max(TOL, float(master.baseline_Turnover.abs().max()) * 2e-12)
    gate_failed = any((
        len(master) != 28, master.semantic_group_id.nunique() != 28, protected_changes != 0,
        max_residuals["cost0_return"] > TOL, max_residuals["cost0_mdd"] > TOL,
        max_residuals["turnover"] > turnover_tolerance, max_residuals["global_BE"] > TOL,
        max_residuals["episode_count"] != 0, max_residuals["be_crossing"] > TOL,
        max_residuals["period_cost"] > TOL, max_residuals["episode_removal"] > TOL,
        any(value > TOL for value in phase4b_residuals.values()), master.phase6b_economic_label.isna().any(),
    ))
    if gate_failed: summary["status"] = "FAILED"
    atomic_json(args.output_root / "phase6b_validation_summary.json", summary)
    archive, sha, members, size = package(args.output_root)
    delivery = {"zip_path": str(archive), "sha256": sha, "member_count": members, "size_bytes": size, "zip_integrity": "PASSED"}
    atomic_json(args.output_root / "phase6b_delivery.json", delivery)
    print(json.dumps({**summary, **delivery}, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
