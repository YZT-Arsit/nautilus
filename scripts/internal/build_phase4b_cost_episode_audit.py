#!/usr/bin/env python3
"""Build Phase 4B cost-stress and episode-concentration evidence from Phase 4A.

This is an additive, read-only analysis of canonical baseline time series.  It
does not run strategies, select parameters, or mutate prior phase artifacts.
"""

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

from scripts.internal.build_phase4a_baseline_evaluation import PERIOD_ORDER
from scripts.internal.build_phase4a_baseline_evaluation import ROOT
from scripts.internal.build_phase4a_baseline_evaluation import TOLERANCE
from scripts.internal.build_phase4a_baseline_evaluation import drawdown
from scripts.internal.build_phase4a_baseline_evaluation import period_label
from scripts.internal.build_phase4a_baseline_evaluation import protected_paths
from scripts.internal.build_phase4a_baseline_evaluation import protected_snapshot


DEFAULT_PHASE4A = ROOT / "outputs/baseline_evaluation/phase4a"
DEFAULT_OUTPUT = ROOT / "outputs/baseline_evaluation/phase4b"
DEFAULT_DELIVERABLES = ROOT / "outputs/deliverables"
COST_GRID = (0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.0, 2.0, 5.0)
LOPO_GRID = (0.0, 0.10, 0.20, 0.30, 0.50)
EPISODE_THRESHOLDS = (0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.0)
DURATION_BUCKETS = (
    ("lt_5m", 0.0, 5 * 60.0),
    ("5m_to_30m", 5 * 60.0, 30 * 60.0),
    ("30m_to_1h", 30 * 60.0, 60 * 60.0),
    ("1h_to_4h", 60 * 60.0, 4 * 60 * 60.0),
    ("4h_to_24h", 4 * 60 * 60.0, 24 * 60 * 60.0),
    ("1d_to_3d", 24 * 60 * 60.0, 3 * 24 * 60 * 60.0),
    ("gt_3d", 3 * 24 * 60 * 60.0, math.inf),
)


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


def finite(value: Any) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def cost_adjusted_increments(gross: np.ndarray, turnover: np.ndarray, cost_bps: float) -> np.ndarray:
    return np.asarray(gross, dtype=np.float64) - np.asarray(turnover, dtype=np.float64) * float(cost_bps) / 10_000.0


def exact_be(total_return: float, turnover: float) -> float:
    return total_return * 10_000.0 / turnover if turnover > 0 else math.nan


def quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q)) if len(values) else math.nan


def remove_top_episodes(episodes: pd.DataFrame, count: int) -> tuple[float, float, float]:
    count = min(max(int(count), 0), len(episodes))
    remaining = episodes.sort_values("delta_gross_return", ascending=False, kind="stable").iloc[count:]
    total_return = float(remaining.delta_gross_return.sum())
    turnover = float(remaining.delta_turnover.sum())
    return total_return, turnover, exact_be(total_return, turnover)


def completed_episode_metrics(episodes: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    returns = episodes.delta_gross_return.to_numpy(float)
    turnovers = episodes.delta_turnover.to_numpy(float)
    be = episodes.break_even_bps.to_numpy(float)
    positive = np.clip(returns, 0.0, None)
    positive_total = float(positive.sum())
    total = float(returns.sum())
    order = np.argsort(-returns, kind="stable")

    def positive_share(count: int) -> float:
        return float(positive[order[: min(count, len(order))]].sum() / positive_total) if positive_total > 0 else math.nan

    def total_share(count: int) -> float:
        return float(returns[order[: min(count, len(order))]].sum() / total) if abs(total) > TOLERANCE else math.nan

    counts = {
        "best1": 1,
        "best5": 5,
        "best1pct": max(1, math.ceil(len(episodes) * 0.01)),
        "best5pct": max(1, math.ceil(len(episodes) * 0.05)),
        "best10pct": max(1, math.ceil(len(episodes) * 0.10)),
    }
    concentration = {
        "episode_count": len(episodes),
        "completed_episode_return": total,
        "completed_episode_turnover": float(turnovers.sum()),
    }
    for key, count in counts.items():
        concentration[f"top_{key}_positive_return_share"] = positive_share(count)
        concentration[f"top_{key}_total_return_share"] = total_share(count)
        remaining_return, remaining_turnover, remaining_be = remove_top_episodes(episodes, count)
        concentration[f"return_without_{key}"] = remaining_return
        concentration[f"turnover_without_{key}"] = remaining_turnover
        concentration[f"BE_without_{key}"] = remaining_be
    concentration.update({
        "top1_episode_positive_return_share": concentration["top_best1_positive_return_share"],
        "top5_episode_positive_return_share": concentration["top_best5_positive_return_share"],
        "top1pct_positive_return_share": concentration["top_best1pct_positive_return_share"],
        "top5pct_positive_return_share": concentration["top_best5pct_positive_return_share"],
        "top10pct_positive_return_share": concentration["top_best10pct_positive_return_share"],
    })

    margin = {
        "episode_count": len(episodes),
        "BE_mean": float(np.mean(be)), "BE_median": float(np.median(be)),
        "BE_p10": quantile(be, .10), "BE_p25": quantile(be, .25),
        "BE_p75": quantile(be, .75), "BE_p90": quantile(be, .90),
        "BE_p95": quantile(be, .95), "BE_max": float(np.max(be)),
    }
    for threshold in EPISODE_THRESHOLDS:
        suffix = f"{threshold:.2f}".replace(".", "_")
        margin[f"count_BE_gt_{suffix}"] = int(np.count_nonzero(be > threshold))
        margin[f"fraction_BE_gt_{suffix}"] = float(np.mean(be > threshold))
    return margin, concentration


def duration_bucket_rows(strategy_id: str, episodes: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start = pd.to_datetime(episodes.start_timestamp, utc=True)
    end = pd.to_datetime(episodes.completion_timestamp, utc=True)
    duration = (end - start).dt.total_seconds().to_numpy(float)
    frame = episodes.copy()
    frame["duration_seconds"] = duration
    summary = {
        "duration_median_seconds": quantile(duration, .50), "duration_p25": quantile(duration, .25),
        "duration_p75": quantile(duration, .75), "duration_p90": quantile(duration, .90),
        "duration_p95": quantile(duration, .95), "duration_p99": quantile(duration, .99),
        "duration_max": float(np.max(duration)),
        "episode_turnover_median": quantile(frame.delta_turnover.to_numpy(float), .50),
        "episode_turnover_p25": quantile(frame.delta_turnover.to_numpy(float), .25),
        "episode_turnover_p75": quantile(frame.delta_turnover.to_numpy(float), .75),
        "episode_turnover_p95": quantile(frame.delta_turnover.to_numpy(float), .95),
        "episode_turnover_max": float(frame.delta_turnover.max()),
    }
    rows: list[dict[str, Any]] = []
    for label, lower, upper in DURATION_BUCKETS:
        child = frame[(frame.duration_seconds >= lower) & (frame.duration_seconds < upper)]
        if child.empty:
            continue
        rows.append({
            "strategy_id": strategy_id, "episode_count": len(frame), **summary,
            "duration_bucket": label, "bucket_episode_count": len(child),
            "bucket_return_median": float(child.delta_gross_return.median()),
            "bucket_BE_median": float(child.break_even_bps.median()),
            "bucket_positive_BE_fraction": float((child.break_even_bps > 0).mean()),
            "bucket_turnover": float(child.delta_turnover.sum()),
        })
    return rows, summary


def spearman_relationships(strategy_id: str, episodes: pd.DataFrame, duration: np.ndarray) -> list[dict[str, Any]]:
    frame = pd.DataFrame({
        "duration": duration, "return": episodes.delta_gross_return.to_numpy(float),
        "turnover": episodes.delta_turnover.to_numpy(float), "be": episodes.break_even_bps.to_numpy(float),
    })
    pairs = (("duration", "return"), ("duration", "be"), ("turnover", "return"), ("turnover", "be"))
    rows: list[dict[str, Any]] = []
    for x, y in pairs:
        x_rank = frame[x].rank(method="average").to_numpy(float)
        y_rank = frame[y].rank(method="average").to_numpy(float)
        rho = float(np.corrcoef(x_rank, y_rank)[0, 1]) if np.std(x_rank) > 0 and np.std(y_rank) > 0 else math.nan
        rows.append({"strategy_id": strategy_id, "x": x, "y": y, "sample_size": len(frame), "spearman_rho": rho})
    return rows


def render_strategy_figures(output: Path, strategy: str, costs: pd.DataFrame, episodes: pd.DataFrame, global_be: float) -> None:
    destination = output / "figures" / strategy
    destination.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 5)); ax.plot(costs.cost_bps, costs.net_return, marker="o")
    ax.axhline(0, color="black", lw=.8); ax.axvline(global_be, color="crimson", ls="--", label=f"Global BE {global_be:.4f} bps")
    ax.set(title=f"{strategy} — Cost Stress", xlabel="Total Cost Assumption (bps)", ylabel="Final Return (1x arithmetic)"); ax.legend(); ax.grid(alpha=.25)
    fig.tight_layout(); fig.savefig(destination / "cost_stress.png", dpi=150); plt.close(fig)

    be = episodes.break_even_bps.to_numpy(float)
    fig, ax = plt.subplots(figsize=(9, 5)); counts, edges = np.histogram(be, bins=60); centers=(edges[:-1]+edges[1:])/2
    ax.hist(be, bins=edges, alpha=.45, label="Histogram"); ax.plot(centers, counts, color="navy", lw=1, label="Frequency polygon")
    for value in (.10, .20, .30, .50): ax.axvline(value, ls="--", lw=.8, label=f"{value:.2f} bps")
    ax.set(title=f"{strategy} — Episode BE Distribution", xlabel="Signed Episode BE (bps)", ylabel="Episode count"); ax.set_xscale("symlog", linthresh=1.0); ax.legend(ncol=3, fontsize=8); ax.grid(alpha=.2)
    fig.tight_layout(); fig.savefig(destination / "episode_be_distribution.png", dpi=150); plt.close(fig)

    positive = np.clip(episodes.delta_gross_return.to_numpy(float), 0, None); positive=np.sort(positive[positive>0])[::-1]
    cumulative=np.cumsum(positive)/positive.sum() if len(positive) and positive.sum()>0 else np.array([])
    fig, ax = plt.subplots(figsize=(9, 5));
    if len(cumulative): ax.plot(np.arange(1,len(cumulative)+1)/len(cumulative), cumulative)
    ax.plot([0,1],[0,1],ls=":",color="gray"); ax.set(title=f"{strategy} — Positive Episode Return Concentration", xlabel="Fraction of positive episodes, best to worst", ylabel="Cumulative share of positive episode Return"); ax.grid(alpha=.25)
    fig.tight_layout(); fig.savefig(destination / "episode_return_concentration.png", dpi=150); plt.close(fig)

    start=pd.to_datetime(episodes.start_timestamp,utc=True); end=pd.to_datetime(episodes.completion_timestamp,utc=True); hours=(end-start).dt.total_seconds()/3600
    fig,(left,right)=plt.subplots(1,2,figsize=(12,4)); left.scatter(end,hours,s=3,alpha=.25); left.set_yscale("symlog",linthresh=1/60); left.set(xlabel="Completion time",ylabel="Holding duration (hours)")
    right.hist(hours,bins=60,alpha=.55); right.set_xscale("symlog",linthresh=1/60); right.axvline(hours.median(),color="crimson",ls="--",label=f"median {hours.median():.2f}h"); right.axvline(hours.quantile(.95),color="darkorange",ls="--",label=f"P95 {hours.quantile(.95):.2f}h"); right.set(xlabel="Holding duration (hours)",ylabel="Episode count"); right.legend(fontsize=8)
    fig.suptitle(f"{strategy} — Holding Duration"); fig.tight_layout(); fig.savefig(destination / "holding_duration.png",dpi=150); plt.close(fig)


def render_aggregate_figures(output: Path, stress: pd.DataFrame, triage: pd.DataFrame) -> None:
    root=output/"figures"; root.mkdir(parents=True,exist_ok=True)
    pivot=stress.pivot(index="representative_strategy_id",columns="cost_bps",values="positive_period_count")/10.0
    fig,ax=plt.subplots(figsize=(12,7)); image=ax.imshow(pivot.to_numpy(),aspect="auto",vmin=0,vmax=1,cmap="RdYlGn"); ax.set_xticks(range(len(pivot.columns)),[f"{x:g}" for x in pivot.columns]); ax.set_yticks(range(len(pivot.index)),pivot.index); ax.set(xlabel="Total cost assumption (bps)",title="Positive Half-Year Fraction under Cost Stress"); fig.colorbar(image,ax=ax,label="Positive half-year fraction"); fig.tight_layout(); fig.savefig(root/"survival_heatmap.png",dpi=160); plt.close(fig)
    for x,y,title,name,xlabel,ylabel in (
        ("global_BE","episode_BE_median","Global BE vs Median Episode BE","global_vs_episode_be.png","Global BE (bps)","Median Episode BE (bps)"),
        ("baseline_return","return_at_0_20bps","Baseline vs 0.20-bps Stressed Return","baseline_vs_020bps_return.png","Baseline Return (1x)","0.20-bps Return (1x)"),
    ):
        fig,ax=plt.subplots(figsize=(8,6)); ax.scatter(triage[x],triage[y]);
        for row in triage.itertuples(): ax.annotate(row.strategy_id,(getattr(row,x),getattr(row,y)),fontsize=7,xytext=(3,3),textcoords="offset points")
        ax.axhline(0,color="gray",lw=.8); ax.axvline(0,color="gray",lw=.8); ax.set(title=title,xlabel=xlabel,ylabel=ylabel); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(root/name,dpi=160); plt.close(fig)


def package(output: Path, deliverables: Path) -> tuple[Path, str]:
    deliverables.mkdir(parents=True,exist_ok=True); target=deliverables/"phase4b_cost_episode_audit.zip"; temporary=target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file(): archive.write(path,Path("phase4b_cost_episode_audit")/path.relative_to(output))
    os.replace(temporary,target)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None: raise ValueError("ZIP integrity failure")
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--phase4a-root",type=Path,default=DEFAULT_PHASE4A); parser.add_argument("--output-root",type=Path,default=DEFAULT_OUTPUT); parser.add_argument("--deliverable-root",type=Path,default=DEFAULT_DELIVERABLES); args=parser.parse_args()
    args.output_root.mkdir(parents=True,exist_ok=True)
    protection=protected_paths(args.deliverable_root)+[args.phase4a_root]
    before=protected_snapshot(protection); atomic_json(args.output_root/"phase4b_protected_hashes_before.json",before)
    master=pd.read_csv(args.phase4a_root/"phase4a_strategy_master.csv")
    semantic=pd.read_csv(args.phase4a_root/"phase4a_semantic_group_summary.csv")
    scope=master[master.baseline_tier.isin(["A","B"])].sort_values(["baseline_tier","strategy_id"]).drop_duplicates("executable_evidence_group_id")
    if len(scope)!=13 or scope.executable_evidence_group_id.nunique()!=13: raise ValueError(f"expected 13 A/B groups, got {len(scope)}")
    members=master.groupby("executable_evidence_group_id").strategy_id.apply(lambda x:";".join(sorted(x))).to_dict()
    scope_rows=[]; stress_rows=[]; period_rows=[]; lopo_rows=[]; margin_rows=[]; concentration_rows=[]; duration_rows=[]; relationship_rows=[]; triage_rows=[]
    max_be_residual=max_cost0_return=max_cost0_mdd=max_cost0_turnover=max_episode_removal_residual=0.0
    accounting_sums={"slippage_return":0.0,"funding_return":0.0,"trading_return":0.0,"gross_price_return":0.0}
    for item in scope.itertuples():
        ts=pd.read_parquet(Path(item.source_timeseries),columns=["event_time_ns","normal_gross_price_return","normal_slippage_return","normal_trading_return","normal_funding_return","normal_total_return","normal_turnover"])
        ts["period"]=period_label(ts.event_time_ns); gross=ts.normal_total_return.to_numpy(float); turnover=ts.normal_turnover.to_numpy(float)
        for key in accounting_sums: accounting_sums[key]+=float(ts[f"normal_{key}"].sum())
        episode=pd.read_csv(Path(item.source_episode_table)); episode=episode[episode.premium_mode=="included"].copy()
        if episode.empty: raise ValueError(f"{item.strategy_id}: no completed included-premium episodes")
        margin,concentration=completed_episode_metrics(episode); duration, duration_summary=duration_bucket_rows(item.strategy_id,episode)
        duration_rows.extend({**row,"semantic_group_id":item.executable_evidence_group_id} for row in duration)
        start=pd.to_datetime(episode.start_timestamp,utc=True); end=pd.to_datetime(episode.completion_timestamp,utc=True); duration_values=(end-start).dt.total_seconds().to_numpy(float)
        relationship_rows.extend(spearman_relationships(item.strategy_id,episode,duration_values))
        margin_rows.append({"strategy_id":item.strategy_id,"semantic_group_id":item.executable_evidence_group_id,**margin})
        concentration_rows.append({"strategy_id":item.strategy_id,"semantic_group_id":item.executable_evidence_group_id,**concentration})
        scope_rows.append({"semantic_group_id":item.executable_evidence_group_id,"representative_strategy_id":item.strategy_id,"member_strategy_ids":members[item.executable_evidence_group_id],"phase4a_tier":item.baseline_tier,"canonical_config":item.canonical_baseline_config,"timeframe":item.timeframe,"realistic_lag":item.canonical_realistic_lag,"semantic_provenance":item.semantic_provenance})
        strategy_stress=[]
        for cost in COST_GRID:
            increments=cost_adjusted_increments(gross,turnover,cost); period_net=ts.assign(net=increments).groupby("period",sort=False).agg(net_return=("net","sum"),turnover=("normal_turnover","sum"))
            net=float(increments.sum()); row={"semantic_group_id":item.executable_evidence_group_id,"representative_strategy_id":item.strategy_id,"phase4a_tier":item.baseline_tier,"cost_bps":cost,"cost_interpretation":"TOTAL_COST_ASSUMPTION_FEE0_BASELINE","net_return":net,"MDD":drawdown(increments),"turnover":float(turnover.sum()),"positive_period_count":int((period_net.net_return>TOLERANCE).sum()),"negative_period_count":int((period_net.net_return<-TOLERANCE).sum()),"LOPO_positive":bool(all(net-v>TOLERANCE for v in period_net.net_return)),"minimum_LOPO_return":float(min(net-v for v in period_net.net_return)),"global_BE_bps":exact_be(float(gross.sum()),float(turnover.sum())),"survives_cost":net>TOLERANCE}
            stress_rows.append(row); strategy_stress.append(row)
            for period in PERIOD_ORDER:
                child=period_net.loc[period] if period in period_net.index else None
                if child is not None: period_rows.append({"strategy_id":item.strategy_id,"semantic_group_id":item.executable_evidence_group_id,"cost_bps":cost,"period":period,"net_return":float(child.net_return),"turnover":float(child.turnover),"positive":float(child.net_return)>TOLERANCE})
        for cost in LOPO_GRID:
            adjusted=cost_adjusted_increments(gross,turnover,cost); values=pd.DataFrame({"period":ts.period,"value":adjusted}).groupby("period").value.sum(); total=float(values.sum()); leave=total-values.to_numpy(float)
            lopo_rows.append({"strategy_id":item.strategy_id,"semantic_group_id":item.executable_evidence_group_id,"cost_bps":cost,"LOPO_positive":bool(np.all(leave>TOLERANCE)),"minimum_LOPO_return":float(np.min(leave))})
        cost_frame=pd.DataFrame(strategy_stress); render_strategy_figures(args.output_root,item.strategy_id,cost_frame,episode,float(item.be_realistic_lag))
        zero=cost_frame[cost_frame.cost_bps==0].iloc[0]; max_cost0_return=max(max_cost0_return,abs(float(zero.net_return)-float(item.return_realistic_lag))); max_cost0_mdd=max(max_cost0_mdd,abs(float(zero.MDD)-float(item.mdd_realistic_lag))); max_cost0_turnover=max(max_cost0_turnover,abs(float(zero.turnover)-float(item.turnover_realistic_lag)))
        max_be_residual=max(max_be_residual,abs(float(gross.sum())-float(turnover.sum())*float(item.be_realistic_lag)/10_000))
        # Removal identity verifies Return and Turnover come from the same surviving population.
        for key in ("best1","best5","best1pct","best5pct"):
            max_episode_removal_residual=max(max_episode_removal_residual,abs(concentration[f"return_without_{key}"]-concentration[f"turnover_without_{key}"]*concentration[f"BE_without_{key}"]/10_000))
        at={float(r["cost_bps"]):r for r in strategy_stress}; labels=[]
        if item.be_realistic_lag<=.10 or not at[.10]["survives_cost"]: labels.append("COST_FRAGILE")
        winner_concentrated=bool(concentration["top_best5pct_positive_return_share"]>=.50 or concentration["return_without_best5pct"]<=0)
        labels.append("WINNER_CONCENTRATED" if winner_concentrated else "WINNER_DISTRIBUTED")
        episode_broad=margin["fraction_BE_gt_0_00"]>=.50 and concentration["return_without_best5pct"]>0
        if episode_broad: labels.append("EPISODE_BROAD")
        if not at[.10]["LOPO_positive"]: labels.append("TEMPORALLY_FRAGILE_AT_0_10BPS")
        followup="HIGH" if at[.10]["survives_cost"] and at[.10]["LOPO_positive"] and not winner_concentrated else ("MEDIUM" if at[.10]["survives_cost"] else "LOW")
        triage_rows.append({"strategy_id":item.strategy_id,"semantic_group_id":item.executable_evidence_group_id,"phase4a_tier":item.baseline_tier,"baseline_return":item.return_realistic_lag,"global_BE":item.be_realistic_lag,"MDD":item.mdd_realistic_lag,"turnover":item.turnover_realistic_lag,"cost_at_which_return_crosses_zero":item.be_realistic_lag,"survives_0_10bps":at[.10]["survives_cost"],"survives_0_20bps":at[.20]["survives_cost"],"survives_0_30bps":at[.30]["survives_cost"],"survives_0_50bps":at[.50]["survives_cost"],"return_at_0_10bps":at[.10]["net_return"],"return_at_0_20bps":at[.20]["net_return"],"return_at_0_30bps":at[.30]["net_return"],"return_at_0_50bps":at[.50]["net_return"],"positive_periods_at_0":at[0]["positive_period_count"],"positive_periods_at_0_10":at[.10]["positive_period_count"],"positive_periods_at_0_20":at[.20]["positive_period_count"],"positive_periods_at_0_30":at[.30]["positive_period_count"],"positive_periods_at_0_50":at[.50]["positive_period_count"],"episode_BE_median":margin["BE_median"],"episode_positive_BE_fraction":margin["fraction_BE_gt_0_00"],"top5pct_positive_return_share":concentration["top_best5pct_positive_return_share"],"return_without_top5pct":concentration["return_without_best5pct"],"BE_without_top5pct":concentration["BE_without_best5pct"],"holding_duration_median":duration_summary["duration_median_seconds"],"holding_duration_p95":duration_summary["duration_p95"],"phase4b_labels":";".join(labels),"followup_priority":followup})
        print(f"ANALYZED {item.strategy_id}",flush=True)
    scope_frame=pd.DataFrame(scope_rows); stress=pd.DataFrame(stress_rows); periods=pd.DataFrame(period_rows); lopo=pd.DataFrame(lopo_rows); margins=pd.DataFrame(margin_rows); concentrations=pd.DataFrame(concentration_rows); durations=pd.DataFrame(duration_rows); relationships=pd.DataFrame(relationship_rows); triage=pd.DataFrame(triage_rows).sort_values(["followup_priority","global_BE"],ascending=[True,False])
    render_aggregate_figures(args.output_root,stress,triage)
    audit={"canonical_return":"normal_total_return = normal_trading_return + normal_funding_return","trading_return":"gross_price_return + slippage_return","premium_included":"adds funding_return only","exchange_fee_in_canonical_return_bps":0.0,"slippage_in_canonical_return_bps":0.0,"vip0_vip9_columns":"available but excluded from canonical Phase 4A Return","stress_interpretation":"TOTAL_COST_ASSUMPTION","equation":"R_net = R_fee0_funding_included - Turnover_raw * total_cost_bps / 10000","break_even_interpretation":"total admissible transaction cost under fee0/slippage0 baseline","execution_path":"analytical chronological recomputation; no backtest","component_sums_across_scope":accounting_sums}
    atomic_json(args.output_root/"phase4b_cost_accounting_audit.json",audit)
    atomic_csv(args.output_root/"phase4b_strategy_scope.csv",scope_frame); atomic_csv(args.output_root/"phase4b_cost_stress.csv",stress); atomic_csv(args.output_root/"phase4b_period_cost_robustness.csv",periods); atomic_csv(args.output_root/"phase4b_lopo_cost_robustness.csv",lopo); atomic_csv(args.output_root/"phase4b_episode_cost_margin.csv",margins); atomic_csv(args.output_root/"phase4b_episode_concentration.csv",concentrations); atomic_csv(args.output_root/"phase4b_holding_duration_analysis.csv",durations); atomic_csv(args.output_root/"phase4b_episode_relationships.csv",relationships); atomic_csv(args.output_root/"phase4b_shortlist_robustness.csv",triage)
    phase4c=triage[triage.followup_priority.isin(["HIGH","MEDIUM"])].copy(); phase4c["why_cross_symbol_test"]="positive at 0.10-bps diagnostic stress with provenance-preserved baseline"; phase4c["cost_robustness"]=phase4c.phase4b_labels; phase4c["episode_breadth"]=np.where(phase4c.phase4b_labels.str.contains("EPISODE_BROAD"),"BROAD","CONCENTRATED_OR_MIXED"); phase4c["temporal_persistence"]=np.where(phase4c.phase4b_labels.str.contains("TEMPORALLY_FRAGILE"),"FRAGILE","LOPO_POSITIVE_AT_0_10BPS"); phase4c["remaining_warnings"]=phase4c.phase4b_labels
    atomic_csv(args.output_root/"phase4b_phase4c_candidates.csv",phase4c[["strategy_id","semantic_group_id","why_cross_symbol_test","cost_robustness","episode_breadth","temporal_persistence","remaining_warnings"]]); atomic_csv(args.output_root/"phase4b_cost_fragile_candidates.csv",triage[(triage.followup_priority=="LOW")|triage.phase4b_labels.str.contains("COST_FRAGILE|WINNER_CONCENTRATED")])
    primary=triage[triage.strategy_id=="xlsx_s2_0435"].iloc[0].to_dict(); atomic_json(args.output_root/"phase4b_xlsx_s2_0435_deep_dive.json",{k:finite(v) if isinstance(v,(float,np.floating)) else v for k,v in primary.items()})
    rows="".join(f"<tr><td>{html.escape(str(r.strategy_id))}</td><td>{r.phase4a_tier}</td><td>{r.baseline_return:.2%}</td><td>{r.global_BE:.4f}</td><td>{r.return_at_0_10bps:.2%}</td><td>{r.return_at_0_20bps:.2%}</td><td>{html.escape(r.phase4b_labels)}</td></tr>" for r in triage.itertuples())
    document=f"<!doctype html><meta charset='utf-8'><title>Phase 4B Cost and Episode Review</title><h1>Phase 4B — Cost and Episode Review</h1><p>13 independent Tier A/B baseline groups. Canonical baseline parameters, NORMAL/ORIGINAL, Premium Included, realistic lag. No optimization.</p><h2>xlsx_s2_0435</h2><p>Baseline {primary['baseline_return']:.2%}; Global BE {primary['global_BE']:.4f} bps; at 0.10 bps {primary['return_at_0_10bps']:.2%}; at 0.20 bps {primary['return_at_0_20bps']:.2%}; labels {html.escape(primary['phase4b_labels'])}.</p><h2>Shortlist</h2><table border='1' cellpadding='5'><tr><th>Strategy</th><th>Tier</th><th>Baseline Return</th><th>Global BE bps</th><th>Return @0.10</th><th>Return @0.20</th><th>Labels</th></tr>{rows}</table><p>All Return values are 1x arithmetic. Cost scenarios are total-cost assumptions applied analytically to canonical turnover increments.</p>"
    tmp=args.output_root/"phase4b_cost_and_episode_review.html.tmp"; tmp.write_text(document,encoding="utf-8"); os.replace(tmp,args.output_root/"phase4b_cost_and_episode_review.html")
    after=protected_snapshot(protection); atomic_json(args.output_root/"phase4b_protected_hashes_after.json",after); changed=before["aggregate_sha256"]!=after["aggregate_sha256"]
    summary={"status":"PASSED" if not changed else "FAILED","tier_a_groups":int((scope.baseline_tier=="A").sum()),"tier_b_groups":int((scope.baseline_tier=="B").sum()),"semantic_groups_analyzed":len(scope),"cost_grid":list(COST_GRID),"new_strategy_backtests":0,"new_parameter_searches":0,"production_configs_generated":0,"protected_hash_changes":int(changed),"maximum_be_zero_return_residual":max_be_residual,"maximum_cost0_return_residual":max_cost0_return,"maximum_cost0_mdd_residual":max_cost0_mdd,"maximum_cost0_turnover_residual":max_cost0_turnover,"maximum_episode_removal_be_residual":max_episode_removal_residual,"phase4c_candidate_count":len(phase4c),"cost_fragile_or_concentrated_count":len(pd.read_csv(args.output_root/"phase4b_cost_fragile_candidates.csv"))}
    atomic_json(args.output_root/"phase4b_validation_summary.json",summary)
    if summary["status"]!="PASSED" or max(max_be_residual,max_cost0_return,max_cost0_mdd)>1e-9 or max_cost0_turnover>1e-6 or max_episode_removal_residual>1e-9: raise ValueError(summary)
    archive,sha=package(args.output_root,args.deliverable_root); atomic_json(args.output_root/"phase4b_delivery.json",{"zip_path":str(archive),"sha256":sha,"zip_integrity":"PASSED"})
    print(json.dumps({**summary,"zip_path":str(archive),"sha256":sha},indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
