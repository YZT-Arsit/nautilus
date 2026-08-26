#!/usr/bin/env python3
"""Build Phase 4C cross-symbol replication evidence from frozen run outputs."""

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

from scripts.internal.build_phase4a_baseline_evaluation import ROOT
from scripts.internal.build_phase4a_baseline_evaluation import TOLERANCE
from scripts.internal.build_phase4a_baseline_evaluation import drawdown
from scripts.internal.build_phase4a_baseline_evaluation import period_label
from scripts.internal.build_phase4a_baseline_evaluation import protected_paths
from scripts.internal.build_phase4a_baseline_evaluation import protected_snapshot
from scripts.internal.build_phase4b_cost_episode_audit import COST_GRID
from scripts.internal.build_phase4b_cost_episode_audit import completed_episode_metrics
from scripts.internal.build_phase4b_cost_episode_audit import duration_bucket_rows
from scripts.internal.build_phase4b_cost_episode_audit import exact_be
from scripts.internal.prepare_phase4c_cross_symbol import CANDIDATES
from scripts.internal.prepare_phase4c_cross_symbol import COMMON_END_EXCLUSIVE
from scripts.internal.prepare_phase4c_cross_symbol import COMMON_START
from scripts.internal.prepare_phase4c_cross_symbol import REFERENCE
from scripts.internal.prepare_phase4c_cross_symbol import REPLICATION


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


def quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q)) if len(values) else math.nan


def finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def run_path(run_root: Path, symbol: str, strategy: str, timeframe: str, lag: int) -> Path:
    return run_root / symbol / strategy / f"{timeframe}_lag{lag}m"


def compute_case(
    *,
    strategy: str,
    semantic_group: str,
    symbol: str,
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    timeseries = pd.read_parquet(path / "timeseries.parquet")
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    episodes = pd.read_csv(path / "per_trade_break_even.csv")
    gross = timeseries.total_return.to_numpy(float)
    turnover = timeseries.turnover.to_numpy(float)
    total_return = float(gross.sum())
    total_turnover = float(turnover.sum())
    global_be = exact_be(total_return, total_turnover)
    be_residual = abs(total_return - total_turnover * global_be / 10_000.0) if total_turnover > 0 else 0.0
    timeseries["period"] = period_label(timeseries.event_time_ns)
    periods: list[dict[str, Any]] = []
    for label, child in timeseries.groupby("period", sort=True):
        increments = child.total_return.to_numpy(float)
        child_turnover = float(child.turnover.sum())
        child_return = float(increments.sum())
        periods.append({
            "semantic_group_id": semantic_group, "strategy_id": strategy, "symbol": symbol,
            "period": label, "Return": child_return, "Turnover": child_turnover,
            "BE": exact_be(child_return, child_turnover), "MDD": drawdown(increments),
            "Trade Count": int(((child.direction.shift(fill_value=0) != child.direction) & (child.direction != 0)).sum()),
        })
    if episodes.empty:
        margin = {"BE_median": math.nan, "BE_p10": math.nan, "BE_p25": math.nan, "BE_p75": math.nan, "BE_p90": math.nan}
        concentration = {key: math.nan for key in ("top1pct_positive_return_share", "top5pct_positive_return_share", "return_without_best1pct", "return_without_best5pct", "BE_without_best1pct", "BE_without_best5pct")}
        duration_summary = {"duration_median_seconds": math.nan, "duration_p75": math.nan, "duration_p95": math.nan, "duration_max": math.nan, "episode_turnover_median": math.nan}
        duration_rows: list[dict[str, Any]] = []
    else:
        margin, concentration = completed_episode_metrics(episodes)
        duration_rows, duration_summary = duration_bucket_rows(strategy, episodes)
        for row in duration_rows:
            row.update({"semantic_group_id": semantic_group, "symbol": symbol})
    duration_minutes = (
        (pd.to_datetime(episodes.completion_timestamp, utc=True) - pd.to_datetime(episodes.start_timestamp, utc=True)).dt.total_seconds().to_numpy(float) / 60.0
        if not episodes.empty else np.array([])
    )
    episode_return = episodes.delta_gross_return.to_numpy(float) if not episodes.empty else np.array([])
    episode_turnover = episodes.delta_turnover.to_numpy(float) if not episodes.empty else np.array([])
    episode_be = episodes.break_even_bps.to_numpy(float) if not episodes.empty else np.array([])
    completed_return = float(episode_return.sum())
    years = (pd.Timestamp(COMMON_END_EXCLUSIVE, tz="UTC") - pd.Timestamp(COMMON_START, tz="UTC")).total_seconds() / (365.25 * 86400)
    period_positive = sum(row["Return"] > 0 for row in periods)
    cost_rows: list[dict[str, Any]] = []
    for cost in COST_GRID:
        net = gross - turnover * cost / 10_000.0
        cost_rows.append({
            "semantic_group_id": semantic_group, "strategy_id": strategy, "symbol": symbol,
            "cost_bps": cost, "net_return": float(net.sum()), "MDD": drawdown(net),
            "return_positive": bool(net.sum() > 0),
        })
    row = {
        "semantic_group_id": semantic_group, "representative_strategy_id": strategy, "symbol": symbol,
        "is_reference_BTC": symbol == REFERENCE, "common_start": COMMON_START, "common_end": COMMON_END_EXCLUSIVE,
        "Return": total_return, "Turnover": total_turnover, "BE": global_be, "MDD": drawdown(gross),
        "episode_count": len(episodes), "positive_period_count": period_positive, "period_count": len(periods),
        "episode_BE_median": quantile(episode_be, .5), "episode_BE_P25": quantile(episode_be, .25),
        "episode_BE_positive_fraction": float(np.mean(episode_be > 0)) if len(episode_be) else math.nan,
        "episode_return_median": quantile(episode_return, .5), "holding_duration_median": quantile(duration_minutes, .5),
        "holding_duration_P75": quantile(duration_minutes, .75), "holding_duration_P95": quantile(duration_minutes, .95),
        "holding_duration_max": float(duration_minutes.max()) if len(duration_minutes) else math.nan,
        "return_0_10bps": total_return - total_turnover * .10 / 10_000.0,
        "return_0_20bps": total_return - total_turnover * .20 / 10_000.0,
        "return_0_30bps": total_return - total_turnover * .30 / 10_000.0,
        "return_0_50bps": total_return - total_turnover * .50 / 10_000.0,
        "return_1_00bps": total_return - total_turnover * 1.00 / 10_000.0,
        "winner_concentration_top1pct": concentration.get("top1pct_positive_return_share", math.nan),
        "winner_concentration_top5pct": concentration.get("top5pct_positive_return_share", math.nan),
        "return_without_top1pct": concentration.get("return_without_best1pct", math.nan),
        "return_without_top5pct": concentration.get("return_without_best5pct", math.nan),
        "BE_without_top1pct": concentration.get("BE_without_best1pct", math.nan),
        "BE_without_top5pct": concentration.get("BE_without_best5pct", math.nan),
        "episodes_per_year": len(episodes) / years, "turnover_per_year": total_turnover / years,
        "RETURN_POSITIVE": total_return > 0, "BE_POSITIVE": global_be > 0,
        "RETURN_AND_BE_POSITIVE": total_return > 0 and global_be > 0,
        "strategy_config_hash": summary["strategy_config_hash"], "semantic_parameter_changes": summary["semantic_parameter_changes"],
        "max_boundary_notional_error_usdt": summary["max_boundary_notional_error_usdt"],
        "be_formula_residual": be_residual, "episode_be_formula_residual": summary["maximum_break_even_residual"],
        "period_return_residual": abs(sum(item["Return"] for item in periods) - total_return),
        "period_turnover_residual": abs(sum(item["Turnover"] for item in periods) - total_turnover),
        "completed_episode_return": completed_return,
        "replication_flags": ";".join(flag for flag, condition in (("RETURN_POSITIVE", total_return > 0), ("BE_POSITIVE", global_be > 0), ("RETURN_AND_BE_POSITIVE", total_return > 0 and global_be > 0)) if condition) or "NONE",
    }
    episode_summary = {
        "semantic_group_id": semantic_group, "strategy_id": strategy, "symbol": symbol, "episode_count": len(episodes),
        "BE_median": margin.get("BE_median", math.nan), "BE_P10": margin.get("BE_p10", math.nan), "BE_P25": margin.get("BE_p25", math.nan),
        "BE_P75": margin.get("BE_p75", math.nan), "BE_P90": margin.get("BE_p90", math.nan),
        "BE_positive_fraction": float(np.mean(episode_be > 0)) if len(episode_be) else math.nan,
        "BE_gt_0_10_fraction": float(np.mean(episode_be > .10)) if len(episode_be) else math.nan,
        "BE_gt_0_20_fraction": float(np.mean(episode_be > .20)) if len(episode_be) else math.nan,
        "BE_gt_0_30_fraction": float(np.mean(episode_be > .30)) if len(episode_be) else math.nan,
        "BE_gt_0_50_fraction": float(np.mean(episode_be > .50)) if len(episode_be) else math.nan,
        "Return_median": quantile(episode_return, .5), "Turnover_median": quantile(episode_turnover, .5),
        "Holding_median_minutes": duration_summary.get("duration_median_seconds", math.nan) / 60,
        "Holding_P95_minutes": duration_summary.get("duration_p95", math.nan) / 60,
    }
    return row, periods, episode_summary, duration_rows, cost_rows


def replication_label(reference: pd.Series, replication: pd.DataFrame) -> str:
    positive_both = int(replication.RETURN_AND_BE_POSITIVE.sum())
    tested = len(replication)
    if positive_both > tested / 2:
        return "BROAD_REPLICATION"
    if positive_both > 0:
        return "PARTIAL_REPLICATION"
    if bool(reference.RETURN_AND_BE_POSITIVE):
        return "BTC_SPECIFIC"
    return "CROSS_SYMBOL_NEGATIVE"


def make_figures(output: Path, results: pd.DataFrame, stress: pd.DataFrame, durations: pd.DataFrame) -> None:
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    symbols = [REFERENCE, *REPLICATION]
    for strategy, group in results.groupby("representative_strategy_id", sort=False):
        group = group.set_index("symbol").reindex(symbols)
        fig, axes = plt.subplots(2, 3, figsize=(15, 8))
        metrics = (("Return", "Return (1x arithmetic)"), ("BE", "Global signed BE (bps)"), ("MDD", "Max Drawdown"), ("Turnover", "Cumulative Turnover"), ("positive_period_count", "Positive half-years"), ("episode_BE_median", "Median episode BE (bps)"))
        colors = ["#3b82f6", "#9ca3af", "#9ca3af"]
        for axis, (column, title) in zip(axes.flat, metrics, strict=True):
            axis.bar(symbols, group[column], color=colors)
            axis.axhline(0, color="black", lw=.8)
            axis.set_title(title); axis.grid(axis="y", alpha=.2)
        fig.suptitle(f"{strategy} — Frozen Cross-Symbol Replication (BTC reference in blue)")
        fig.tight_layout(); fig.savefig(figures / f"{strategy}_cross_symbol_dashboard.png", dpi=155); plt.close(fig)
    costs = [0, .10, .20, .30, .50, 1.0]
    pivot = stress[stress.cost_bps.isin(costs)].pivot(index=["strategy_id", "symbol"], columns="cost_bps", values="net_return")
    fig, ax = plt.subplots(figsize=(11, 10)); limit=max(abs(pivot.to_numpy()).max(), 1e-9); image=ax.imshow(pivot.to_numpy(), aspect="auto", cmap="RdYlGn", vmin=-limit, vmax=limit)
    ax.set_xticks(range(len(pivot.columns)), [f"{value:g}" for value in pivot.columns]); ax.set_yticks(range(len(pivot.index)), [f"{a}/{b}" for a,b in pivot.index], fontsize=7); ax.set(xlabel="Hypothetical total cost (bps)", title="Phase 4C Cost Survival — Return (1x arithmetic)"); fig.colorbar(image, ax=ax, label="Return"); fig.tight_layout(); fig.savefig(figures / "phase4c_cost_survival_heatmap.png", dpi=160); plt.close(fig)
    be = results.pivot(index="representative_strategy_id", columns="symbol", values="BE").reindex(columns=symbols)
    fig, ax = plt.subplots(figsize=(8, 6)); limit=max(abs(be.to_numpy()).max(), 1e-9); image=ax.imshow(be.to_numpy(), cmap="RdYlGn", vmin=-limit, vmax=limit, aspect="auto")
    ax.set_xticks(range(len(be.columns)), be.columns); ax.set_yticks(range(len(be.index)), be.index); ax.set_title("Signed Global BE Replication (bps)"); fig.colorbar(image, ax=ax); fig.tight_layout(); fig.savefig(figures / "phase4c_be_replication_heatmap.png", dpi=160); plt.close(fig)


def write_html(output: Path, summary: pd.DataFrame, results: pd.DataFrame, deep: pd.DataFrame, validation: dict[str, Any]) -> None:
    label_counts = summary.replication_label.value_counts().to_dict()
    cards = " ".join(f"<b>{html.escape(str(key))}</b>: {value}" for key, value in label_counts.items())
    dashboards = "".join(f'<section><h3>{s}</h3><img src="figures/{s}_cross_symbol_dashboard.png"></section>' for s in CANDIDATES)
    body = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 4C Cross-Symbol Review</title><style>body{{font-family:Arial;margin:28px;max-width:1500px}}table{{border-collapse:collapse;font-size:12px}}th,td{{border:1px solid #ddd;padding:5px}}img{{max-width:100%}}.cards{{padding:14px;background:#f3f4f6}}</style></head><body><h1>Phase 4C — Frozen Cross-Symbol Replication</h1><div class='cards'>Candidate groups: 6 | Transfer safe: 6 | Symbols: BTC reference + 2 replication | {cards}</div><p>Common interval: {COMMON_START} to {COMMON_END_EXCLUSIVE} (right-open). Parameters, direction, premium, and realistic lag are frozen before performance.</p><h2>Replication summary</h2>{summary.to_html(index=False, float_format=lambda x:f'{x:.6g}')}<h2>xlsx_s2_0435 deep dive</h2>{deep.to_html(index=False, float_format=lambda x:f'{x:.6g}')}<h2>All strategy-symbol results</h2>{results.to_html(index=False, float_format=lambda x:f'{x:.6g}')}<h2>Figures</h2><img src='figures/phase4c_cost_survival_heatmap.png'><img src='figures/phase4c_be_replication_heatmap.png'>{dashboards}<h2>Validation</h2><pre>{html.escape(json.dumps(validation, indent=2))}</pre></body></html>"""
    (output / "phase4c_cross_symbol_review.html").write_text(body, encoding="utf-8")


def package(output: Path, deliverables: Path) -> tuple[Path, str]:
    deliverables.mkdir(parents=True, exist_ok=True)
    target = deliverables / "phase4c_cross_symbol_replication.zip"
    temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase4c_delivery.json":
                archive.write(path, path.relative_to(output))
    os.replace(temporary, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError("Phase 4C ZIP integrity failed")
    return target, digest


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root",type=Path,default=ROOT/"outputs/batches/phase4c_cross_symbol")
    parser.add_argument("--phase4a-root",type=Path,default=ROOT/"outputs/baseline_evaluation/phase4a")
    parser.add_argument("--phase4b-root",type=Path,default=ROOT/"outputs/baseline_evaluation/phase4b")
    parser.add_argument("--output-root",type=Path,default=ROOT/"outputs/baseline_evaluation/phase4c")
    parser.add_argument("--deliverables",type=Path,default=ROOT/"outputs/deliverables")
    args=parser.parse_args(); args.output_root.mkdir(parents=True,exist_ok=True)
    audit=pd.read_csv(args.output_root/"phase4c_candidate_transfer_audit.csv").set_index("strategy_id")
    phase4a=pd.read_csv(args.phase4a_root/"phase4a_strategy_master.csv").set_index("strategy_id")
    phase4b=pd.read_csv(args.phase4b_root/"phase4b_phase4c_candidates.csv").set_index("strategy_id")
    result_rows=[]; period_rows=[]; episode_rows=[]; duration_rows=[]; stress_rows=[]
    for strategy in CANDIDATES:
        timeframe=str(audit.loc[strategy,"timeframe"]); lag=int(str(audit.loc[strategy,"realistic_lag"]).split("m",1)[0])
        for symbol in (REFERENCE,*REPLICATION):
            row,periods,episode,durations,costs=compute_case(strategy=strategy,semantic_group=audit.loc[strategy,"semantic_group_id"],symbol=symbol,path=run_path(args.run_root,symbol,strategy,timeframe,lag))
            result_rows.append(row); period_rows.extend(periods); episode_rows.append(episode); duration_rows.extend(durations); stress_rows.extend(costs)
    results=pd.DataFrame(result_rows); periods=pd.DataFrame(period_rows); episodes=pd.DataFrame(episode_rows); durations=pd.DataFrame(duration_rows); stress=pd.DataFrame(stress_rows)
    summaries=[]; loso=[]
    for strategy,group in results.groupby("representative_strategy_id",sort=False):
        reference=group[group.is_reference_BTC].iloc[0]; replication=group[~group.is_reference_BTC].copy(); label=replication_label(reference,replication)
        ratio=replication.episodes_per_year/float(reference.episodes_per_year) if reference.episodes_per_year else pd.Series(np.nan,index=replication.index)
        density_warning=bool(((ratio>10)|(ratio<.1)).any())
        summaries.append({
            "strategy_id":strategy,"phase4a_tier":phase4a.loc[strategy,"baseline_tier"],"phase4b_status":phase4b.loc[strategy,"cost_robustness"],
            "eligible_replication_symbols":len(REPLICATION),"tested_replication_symbols":len(replication),
            "positive_return_symbols":int(replication.RETURN_POSITIVE.sum()),"positive_BE_symbols":int(replication.BE_POSITIVE.sum()),"positive_return_and_BE_symbols":int(replication.RETURN_AND_BE_POSITIVE.sum()),
            "positive_return_fraction":float(replication.RETURN_POSITIVE.mean()),"positive_BE_fraction":float(replication.BE_POSITIVE.mean()),
            "median_symbol_return":float(replication.Return.median()),"median_symbol_BE":float(replication.BE.median()),"median_symbol_MDD":float(replication.MDD.median()),
            "symbols_surviving_0_10bps":int((replication.return_0_10bps>0).sum()),"symbols_surviving_0_20bps":int((replication.return_0_20bps>0).sum()),"symbols_surviving_0_30bps":int((replication.return_0_30bps>0).sum()),
            "BTC_reference_return":reference.Return,"BTC_reference_BE":reference.BE,"replication_label":label,
            "warnings":"SMALL_REPLICATION_N=2"+(";SIGNAL_DENSITY_ORDER_OF_MAGNITUDE" if density_warning else ""),
        })
        for removed in replication.symbol:
            child=replication[replication.symbol!=removed]
            loso.append({"strategy_id":strategy,"removed_symbol":removed,"remaining_symbol_count":len(child),"median_return":float(child.Return.median()),"median_BE":float(child.BE.median()),"positive_fraction":float(child.RETURN_AND_BE_POSITIVE.mean()),"warning":"N=1 descriptive only"})
    replication_summary=pd.DataFrame(summaries)
    results["replication_label"]=results.representative_strategy_id.map(replication_summary.set_index("strategy_id").replication_label)
    deep=results[results.representative_strategy_id=="xlsx_s2_0435"].copy()
    deep["cost_survival_0_10_to_0_30"]=(deep.return_0_10bps>0)&(deep.return_0_20bps>0)&(deep.return_0_30bps>0)
    primary_bucket=durations[durations.strategy_id=="xlsx_s2_0435"].sort_values(["symbol","bucket_BE_median"],ascending=[True,False]).groupby("symbol").head(1)[["symbol","duration_bucket","bucket_BE_median"]].rename(columns={"duration_bucket":"strongest_duration_bucket"})
    deep=deep.merge(primary_bucket,on="symbol",how="left")
    atomic_csv(args.output_root/"phase4c_cross_symbol_results.csv",results)
    atomic_csv(args.output_root/"phase4c_replication_summary.csv",replication_summary)
    atomic_csv(args.output_root/"phase4c_symbol_period_results.csv",periods)
    atomic_csv(args.output_root/"phase4c_episode_replication.csv",episodes)
    atomic_csv(args.output_root/"phase4c_duration_buckets.csv",durations)
    atomic_csv(args.output_root/"phase4c_cost_stress.csv",stress)
    atomic_csv(args.output_root/"phase4c_leave_one_symbol_out.csv",pd.DataFrame(loso))
    atomic_csv(args.output_root/"phase4c_xlsx_s2_0435_deep_dive.csv",deep)
    phase4d=replication_summary[replication_summary.replication_label.isin(["BROAD_REPLICATION","PARTIAL_REPLICATION"])].copy(); phase4d["next_phase"]="EXECUTION_REALISM_AND_CAPACITY_RESEARCH_ONLY"
    nonrep=replication_summary[~replication_summary.strategy_id.isin(phase4d.strategy_id)].copy()
    atomic_csv(args.output_root/"phase4c_phase4d_candidates.csv",phase4d); atomic_csv(args.output_root/"phase4c_nonreplicating_candidates.csv",nonrep)
    make_figures(args.output_root,results,stress,durations)
    before=json.loads((args.output_root/"phase4c_protected_hashes_before.json").read_text(encoding="utf-8")); protection=protected_paths(args.deliverables)+[args.phase4a_root,args.phase4b_root]; after=protected_snapshot(protection); atomic_json(args.output_root/"phase4c_protected_hashes_after.json",after)
    changed=sorted(key for key,value in before["files"].items() if after["files"].get(key)!=value)+sorted(set(after["files"])-set(before["files"]))+sorted(set(before["files"])-set(after["files"]))
    config_hash_counts=results.groupby("representative_strategy_id").strategy_config_hash.nunique(); validation={
        "status":"PASSED" if not changed and len(results)==18 and config_hash_counts.max()==1 else "FAILED",
        "candidate_groups":6,"transferability_decisions":6,"terminal_cases":len(results),"replication_symbols":list(REPLICATION),
        "parameter_search_runs":0,"symbol_specific_parameter_changes":int(results.semantic_parameter_changes.sum()),"lag_optimization_runs":0,"premium_optimization_runs":0,"production_configs_created":0,
        "maximum_global_be_residual":float(results.be_formula_residual.max()),"maximum_episode_be_residual":float(results.episode_be_formula_residual.max()),
        "maximum_period_return_residual":float(results.period_return_residual.max()),"maximum_period_turnover_residual":float(results.period_turnover_residual.max()),
        "maximum_boundary_notional_error_usdt":float(results.max_boundary_notional_error_usdt.max()),"config_hash_variants_per_strategy_max":int(config_hash_counts.max()),
        "protected_artifact_changes":len(changed),"protected_changed_paths":changed,"common_start":COMMON_START,"common_end_exclusive":COMMON_END_EXCLUSIVE,
    }
    atomic_json(args.output_root/"phase4c_validation_summary.json",validation); write_html(args.output_root,replication_summary,results,deep,validation)
    if validation["status"]!="PASSED": raise ValueError(f"Phase 4C validation failed: {validation}")
    archive,digest=package(args.output_root,args.deliverables); atomic_json(args.output_root/"phase4c_delivery.json",{"server_path":str(archive),"size_bytes":archive.stat().st_size,"sha256":digest,"zip_integrity":"PASSED"})
    print(json.dumps({"status":"PASSED","results":len(results),"labels":replication_summary.replication_label.value_counts().to_dict(),"zip":str(archive),"sha256":digest},indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
