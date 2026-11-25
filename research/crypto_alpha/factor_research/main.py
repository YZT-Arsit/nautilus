"""Frozen Foundation factor research, with selection sealed before historical evaluation."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT.parents[1])]

import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from data.market import symbol_dir
from factor_research.ablation import ablate, historical, sets
from factor_research.inventory import inventory, real_causality
from factor_research.redundancy import correlation, select, stability
from factor_research.statistics import analyze, decay, distribution
from foundation.data import NautilusMarket
from foundation.dataset import build
from foundation.main import panel_for
from foundation.registry import Registry
from foundation.test_runner import execute
from strategy_framework.execution.backtest_report import write_artifact_manifest
from training.protocol import annual_windows
from training.run import code_version, funding_inputs
from v3.common import digest, require_space, utc, write_json

HISTORY = "OBSERVED_HISTORICAL_TEST"
VALIDATION = "INTERNAL_VALIDATION"
TRAIN = "TRAIN"


def freeze(foundation, expected):
    manifest = foundation/"artifact_manifest.json"
    files = json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]
    for f in files:
        if digest(foundation/f["path"]) != f["sha256"]:
            raise ValueError(f"Foundation result differs from its manifest: {f['path']}")
    actual = json.loads((foundation/"run_environment.json").read_text(encoding="utf-8"))["commit"]
    if actual != expected:
        raise ValueError(f"Foundation data was built from {actual}, expected {expected}")
    frozen_code = json.loads((ROOT/"factor_research/foundation_code_sha256.json").read_text(encoding="utf-8"))
    for name, expected_hash in frozen_code.items():
        if digest(ROOT/name) != expected_hash:
            raise ValueError(f"Foundation code or config changed: {name}")
    return dict(commit=actual,manifest_sha256=digest(manifest),artifacts=len(files),
                code_files=len(frozen_code),dataset_sha256=next(f["sha256"] for f in files if f["path"]=="dataset.parquet"))


def coverage_only(cfg,out):
    rows, symbol_dates = [], []
    cutoff = cfg["fresh_metadata_start"]
    for s in cfg["symbols"]:
        for kind,pattern in (("bars","data_type=bar/freq=1m/date=*"),("funding","data_type=funding_rate/freq=*/date=*")):
            dates = {p.name[5:] for p in symbol_dir((ROOT/cfg["data_root"]).resolve(),s).glob(pattern)
                     if p.name[5:]>=cutoff and any(p.glob("*.parquet"))}
            if kind=="bars":
                symbol_dates.append(dates)
            rows.append(dict(split="FRESH_UNTOUCHED_TEST",symbol=s,kind=kind,from_date=cutoff,partitions=len(dates),
                             first_date=min(dates,default=""),last_date=max(dates,default=""),
                             coverage_type="Nautilus directory metadata only; no prices/funding rates read"))
    common = sorted(set.intersection(*symbol_dates))
    pd.DataFrame(rows).to_csv(out/"fresh_coverage_metadata.csv",index=False)
    pd.DataFrame({"date":common}).to_csv(out/"fresh_common_bar_directories.csv",index=False)
    return len(common)


def make_views(raw, names, window, cfg):
    panels, slices = {}, {}
    for h in cfg["horizons"]:
        # Prices are frozen exact completed closes. Rebuild six independent outcomes.
        p0,p1 = raw.label_entry_price,raw[f"close_end_{h}"]
        own = (p1/p0-1).where(p0.gt(0)&p1.gt(0))
        np.testing.assert_allclose(own,raw[f"return_{h}"],equal_nan=True,rtol=1e-12,atol=1e-12)
        # Foundation's task consumes each distinct audited outcome column.
        raw[f"return_{h}"] = own
        cols = ["prediction_time","symbol","eligible","feature_available_time","label_entry_price",
                "execution_price_proxy",f"return_{h}",f"close_end_{h}","open_end_60",*names]
        if "btc_beta" not in names:
            cols.append("btc_beta")
        panel = panel_for(raw[cols],names,cfg,holding=h,interval=60)
        panels[h] = panel
        slices[h] = {TRAIN:panel.view(window.train_start,window.validation_start),
                     VALIDATION:panel.view(window.validation_start,window.test_start)}
    return panels,slices


def run(cfg,foundation,out,focus):
    start_time = perf_counter()
    require_space(out,cfg)
    before = freeze(foundation,cfg["foundation_commit"])
    print("FOUNDATION_FROZEN",before,flush=True)
    test = execute(ROOT)
    write_json(out/"tests.json",test)
    if test["exit_code"]:
        raise RuntimeError("unit tests failed")
    registry = Registry()
    plugins = cfg.get("factor_plugins",[])
    for module in plugins:
        import_module(module).register(registry)
    if plugins:
        raw, source, funding, proxies, *_ = build(NautilusMarket(cfg,ROOT),cfg,registry)
    else:
        source = pd.read_csv(foundation/"factor_registry.csv")
        raw = pd.read_parquet(foundation/"dataset.parquet").reset_index()
        funding,proxies = funding_inputs(foundation)
    if focus:
        source = source.loc[source.factor_name.eq(focus)].copy()
        if source.empty:
            raise ValueError(f"unknown registered factor {focus}")
    reg = inventory(source)
    names = reg.factor_name.tolist()
    print("FACTORS",len(names),flush=True)
    causal = real_causality(cfg,registry,names,out)
    names_without_causality_evidence = causal.loc[causal.valid_pairs.eq(0),"factor_name"].tolist()
    reg = reg.merge(causal[["factor_name","causality_check","valid_pairs"]],on="factor_name",validate="one_to_one")
    reg.loc[reg.valid_pairs.eq(0),"causality_check"] = "NO_FINITE_REAL_PROBE_PAIRS"
    start,end = [pd.Timestamp(cfg[d],tz="UTC") for d in ("start","end")]
    window, = annual_windows(start,end,1,cfg["validation_months"])
    panels,views = make_views(raw,names,window,cfg)
    del raw
    h = cfg["main_horizon"]
    table, months, hourly, q, temporal = analyze(views,names,cfg)
    print("TRAIN_VALIDATION_DIAGNOSTICS_COMPLETE",flush=True)
    stats = stability(table,names,cfg)
    corr = correlation(views[h][TRAIN],names,temporal[h,TRAIN],cfg["min_assets"])
    combined = []
    for kind,frame in corr.items():
        frame.rename_axis(index="factor",columns="other_factor").stack(future_stack=True).rename(
            "correlation").reset_index().assign(method=kind).to_csv(out/f"correlation_{kind}.csv",index=False)
        combined.append(frame.rename_axis(index="factor",columns="other_factor").stack(future_stack=True).rename(
            "correlation").reset_index().assign(method=kind))
    pd.concat(combined,ignore_index=True).to_csv(out/"factor_correlation.csv",index=False)
    clusters, reps, stable = select(corr,stats,cfg)
    clusters.to_csv(out/"factor_clusters.csv",index=False)
    print("CORRELATION_AND_CLUSTERING_COMPLETE",flush=True)
    candidates = sets(reg,reps,stable) if not focus else {"full":names}
    candidates = {k:v for k,v in candidates.items() if v}
    model_params = {**cfg["models"],"LightGBM":{**cfg["models"]["LightGBM"],"n_estimators":cfg["lightgbm_round_limit"]}}
    scores, means = ablate(panels[h],window,model_params,candidates,cfg,out)
    winner = means.index[0]
    prespecified = decay(table,cfg)
    best_horizons = dict(zip(prespecified.factor,prespecified.best_training_horizon))
    lock = dict(foundation=before, selection_cutoff=str(window.test_start), selected_utc=utc(),
                chosen_by="largest mean validation Pearson IC across the three fixed model families",
                chosen_set=winner, sets=candidates, best_training_horizons=best_horizons, train_only_direction={
                    name:int(stats.loc[stats.factor.eq(name),"train_direction"].iloc[0]) for name in names},
                fresh_data="metadata only",validation_ic_full=float(means.loc["full","mean_ic"]),
                validation_ic_selected=float(means.loc[winner,"mean_ic"]),
                incremental_ic=float(means.loc[winner,"incremental_ic"]), 
                caution="selection uses internal validation; historical test was already observed")
    write_json(out/"selected_factor_sets.json",lock)
    print("SELECTION_FROZEN",winner,flush=True)
    # Outcomes from the already observed second year are evaluated only after the lock.
    historical_views = {i:{HISTORY:panels[i].view(window.test_start,window.test_end,mature=False)} for i in views}
    historical_table, hm, hh, hq, _ = analyze(historical_views,names,cfg,orientation=stats.set_index("factor").loc[names,"train_direction"].to_numpy())
    table = pd.concat([table,historical_table],ignore_index=True)
    months = pd.concat([months,hm],ignore_index=True)
    table.to_csv(out/"factor_ic.csv",index=False)
    months.to_csv(out/"monthly_ic.csv",index=False)
    pd.concat([hourly,hh],ignore_index=True).to_parquet(out/"factor_ic_by_section.parquet",index=False,compression="zstd")
    pd.concat([q,hq],ignore_index=True).to_csv(out/"quantile_returns.csv",index=False)
    decay_table = decay(table,cfg)
    decay_table.to_csv(out/"ic_decay.csv",index=False)
    transformed = distribution({**views[h],**historical_views[h]},names,cfg)
    transformed.to_csv(out/"factor_statistics.csv",index=False)
    raw_stats = transformed.query("split=='TRAIN' and transformation=='raw'")
    reg = reg.merge(raw_stats[["factor","constant","constant_sections","outlier_rate_iqr3","missing_rate"]],
                    left_on="factor_name",right_on="factor",validate="one_to_one").drop(columns="factor")
    reg.to_csv(out/"factor_registry.csv",index=False)
    historical(panels[h],window,model_params,candidates,winner,cfg,funding,proxies,out)
    common_days = coverage_only(cfg,out)
    from factor_research.report import deliver
    summary = deliver(out,cfg,reg,table,months,corr,clusters,means,lock,common_days,test)
    assert freeze(foundation,cfg["foundation_commit"])==before
    summary.update(foundation=before,git_commit=code_version(),device="CPU",host=platform.node(),
                   elapsed_seconds=perf_counter()-start_time,production_orders=0,demo_orders=0,
                   causal_factors_with_finite_probe=len(names)-len(names_without_causality_evidence))
    write_json(out/"summary.json",summary)
    write_json(out/"resolved_config.json",cfg)
    write_artifact_manifest(out,"factor_research_v1")
    print("FACTOR_RESEARCH_COMPLETE",flush=True)


def settings(path,focus):
    spec = json.loads(path.read_text(encoding="utf-8"))
    foundation = (ROOT/spec["foundation"]).resolve()
    cfg = {**json.loads((foundation/"resolved_config.json").read_text(encoding="utf-8")),**spec}
    cfg["output"] = spec["output"]
    cfg["epochs"] = cfg["model_epochs"]
    if cfg["main_horizon"] != 60 or cfg["delay_minutes"] != 10:
        raise ValueError("Phase 2 comparison uses the Foundation 60-minute main outcome and 10-minute execution delay")
    out = (ROOT/cfg["output"]).resolve()
    if out.parent!=foundation.parent or not out.name.startswith("factor_research_"):
        raise ValueError("results must use a separate factor_research_* directory")
    if focus:
        if not focus.isidentifier():
            raise ValueError("registered factor name must be an identifier")
        out = out.with_name(f"{out.name}_single_{focus}")
    out.mkdir(parents=True,exist_ok=True)
    return cfg,foundation,out


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=ROOT/"configs/factor_research_v1.json")
    parser.add_argument("--factor")
    args=parser.parse_args()
    cfg,foundation,output=settings(args.config,args.factor)
    if os.name=="nt":
        from ctypes import WinDLL, wintypes
        kernel=WinDLL("kernel32")
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetPriorityClass.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    with threadpool_limits(limits=cfg["threads"]):
        run(cfg,foundation,output,args.factor)
