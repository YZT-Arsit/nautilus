"""Purged model/factor experiments. This stage never opens the fresh prices."""
from dataclasses import asdict,replace
import os
import shutil
from time import perf_counter
import warnings

import joblib
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from v3.common import ROOT,digest,inputs,require_space,utc,verify_prior,write_json
from v3.features import GROUPS,historical
from v3.objectives import Trial,diagnostics,fit_trial,score_view,target
from v3.portfolio import full_cost,replay,variants
from v3.quality import quality
from training.protocol import Panel,factor_diagnostics
from v2.common import book,cube
from v2.execution import calibrate
from v2.features import horizon_panel


def calendar(cfg,year_delta=0):
    delta=pd.DateOffset(years=year_delta)
    start=pd.Timestamp(cfg["fit_start"],tz="UTC")+delta
    edges=[pd.Timestamp(d,tz="UTC")+delta for d in cfg["validation_edges"]]
    return start,edges


def inputs_for(frame,names,h,cfg):
    source=horizon_panel(frame,h,h,cfg["delay_minutes"],cfg["min_assets"],cfg["label_epsilon"])
    panel=Panel(source,cfg["symbols"],names,cfg["min_assets"])
    panel.cadence=h
    return source,panel


def folds(panel,cfg,shift=0):
    start,edges=calendar(cfg,shift)
    for a,b in zip(edges[:-1],edges[1:]):
        fit_end=a-pd.Timedelta(minutes=cfg["embargo_minutes"])
        train=panel.view(start,fit_end)
        val=panel.view(a,b,mature=False)
        assert (train["available"][np.isfinite(train["raw"])]<=fit_end.value).all()
        yield train,val,a,b


def candidates(cfg):
    seed=cfg["seed"]
    base=[Trial(f"{f}_4h_{l}_{y}",f,240,l,y,seed=seed)
          for f,l,y in [
              ("Ridge","MSE","rank"),("LightGBM","MSE","rank"),("MLP","MSE","rank"),
              ("GRU","IC","rank"),("StockMixer","IC","rank"),("MASTER","IC","rank"),
              ("MLP","IC","rank"),("MLP","IC_HUBER","rank"),("MLP","IC_TEMP","rank"),
              ("MASTER","MSE","rank"),("MASTER","IC_HUBER","rank"),("MASTER","IC_TEMP","rank"),
              ("MLP","MSE","raw"),("MLP","MSE","zscore"),("MLP","MSE","residual"),
              ("MLP","IC","raw"),("MLP","IC","zscore"),("MLP","IC","residual"),
          ]]
    base.extend(Trial(f"{f}_{h}m_{l}",f,h,l,seed=seed)
                for h in (60,480,1440) for f,l in (("LightGBM","MSE"),("MLP","IC")))
    ablations=[Trial("ablation_full","Ridge",subset="full",seed=seed),
               Trial("ablation_selected","Ridge",subset="selected",seed=seed),
               Trial("ablation_legacy88","Ridge",subset="legacy",seed=seed)]
    for group in GROUPS:
        ablations.extend(Trial(f"ablation_{kind}_{group}","Ridge",subset=f"{kind}:{group}",seed=seed)
                         for kind in ("drop","only"))
    return base,ablations


def columns(panel,registry,scanned,subset,train):
    fit=train["x"]
    eligible=(np.isfinite(fit).mean(axis=(0,1))>=.40)&(np.nanstd(fit,axis=(0,1))>0)
    if subset=="selected":
        return [panel.factors.index(f) for f in scanned]
    names=set(panel.factors)
    if subset=="legacy":
        names={f for f in names if not f.startswith("v3_")}
    if ":" in subset:
        kind,group=subset.split(":",1)
        matching=set(registry.loc[registry.group==group,"factor_name"])
        names=names&matching if kind=="only" else names-matching
    return [i for i,x in enumerate(panel.factors) if x in names and eligible[i]]


def save_model(out,key,model,prep):
    p=out/"models"
    p.mkdir(exist_ok=True)
    if isinstance(model,torch.nn.Module):
        weights=p/f"{key}.pt"
        torch.save(model.state_dict(),weights)
    elif hasattr(model,"save_model"):
        weights=p/f"{key}.txt"
        model.save_model(str(weights))
    else:
        weights=p/f"{key}.joblib"
        joblib.dump(model,weights)
    processor=p/f"{key}.preprocessor.joblib"
    joblib.dump(prep,processor)
    return weights,processor


def metrics(trial,scores,val,panel):
    return diagnostics(scores,val,target(val,panel.factors,trial.label))


def validate(trial,prepared,screens,registry,funding,cfg):
    source,panel=prepared[trial.horizon]
    rows=[]
    oof=[]
    histories=[]
    for n,(train,val,a,b) in enumerate(folds(panel,cfg),1):
        cols=columns(panel,registry,screens[trial.horizon,n],trial.subset,train)
        model,_,x,sec,curve=fit_trial(trial,panel,cols,train,cfg)
        scores=score_view(model,trial.family,x,val)
        ic,series,rank=metrics(trial,scores,val,panel)
        economics,_,_=replay(source,panel,val,scores,{"rule":"rank"},0,funding,cfg)
        rows.append(dict(**asdict(trial),fold=n,start=a,end=b,features=len(cols),fit_seconds=sec,
                         economic_positive=economics["net_return"]>0,validation_net_6bps=economics["net_return"],**ic))
        histories.extend(dict(name=trial.name,seed=trial.seed,fold=n,epoch=i,loss=l) for i,l in curve)
        oof.append((val,scores))
    d=pd.DataFrame(rows)
    aggregate={**asdict(trial),"mean_ic":d.ic.mean(),"sd_ic":d.ic.std(),"worst_ic":d.ic.min(),
               "mean_rank_ic":d.rank_ic.mean(),"mean_processed_ic":d.processed_ic.mean(),
               "mean_icir":d.icir.mean(),"average_net_6bps":d.validation_net_6bps.mean(),
               "worst_net_6bps":d.validation_net_6bps.min(),"fit_seconds":d.fit_seconds.sum()}
    aggregate["selection_score"]=aggregate["mean_ic"]-.20*aggregate["sd_ic"]
    return rows,aggregate,oof,histories


def audit_versions(v1,v2,out):
    s1=pd.read_csv(v1/"frequency_comparison.csv")
    s2=pd.read_csv(v2/"model_comparison.csv")
    v1row=s1.loc[(s1.interval_minutes==240)&(s1.split=="validation")].iloc[0]
    v2row=s2.loc[(s2.prediction_minutes==240)&(s2.label_minutes==240)&s2.split.eq("validation")].sort_values("ic").iloc[-1]
    pd.DataFrame([
        dict(version="v1",metric="raw-return per-timestamp Pearson",validation_time="2025-04-01 through 2025-07-01 cutoff",
             criterion="within-frequency max validation IC; overall validation IC",comparison_model=v1row.model,ic=v1row.ic),
        dict(version="v2",metric="raw-return per-timestamp Pearson",validation_time="2025-04-01 through 2025-07-01 cutoff",
             criterion="max validation IC for model; separate net 6bps decision (>=10 fills) for portfolio",
             comparison_model=v2row.model,ic=v2row.ic),
        dict(version="v3",metric="raw-return per-timestamp Pearson",validation_time="three expanding purged folds Oct 2024–Jun 2025",
             criterion="mean IC - .2 fold SD; portfolio validation net 6bps"),
    ]).to_csv(out/"definition_audit.csv",index=False)


def study():
    cfg,v1,v2,out=inputs()
    started=perf_counter()
    warnings.filterwarnings("ignore",message="Mean of empty slice")
    warnings.filterwarnings("ignore",message="invalid value encountered in expm1")
    if os.name=="nt":
        from ctypes import WinDLL,wintypes
        kernel=WinDLL("kernel32")
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetPriorityClass.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(),16384):
            raise OSError("cannot lower research priority")
    freeze=verify_prior(v1,v2)
    based=ROOT/"code_manifest.json"
    commit=__import__("json").loads(based.read_text())["git_commit"] if based.exists() else ""
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    frame,registry=historical(v2)
    registry.to_csv(out/"factor_registry.csv",index=False)
    audit_versions(v1,v2,out)
    quality(frame,registry,cfg,out)
    print("FACTOR QUALITY COMPLETE",flush=True)
    names=registry.factor_name.tolist()
    prepared={h:inputs_for(frame,names,h,cfg) for h in (60,240,480,1440)}
    screens={}
    for h,(source,panel) in prepared.items():
        for n,(train,val,a,b) in enumerate(folds(panel,cfg),1):
            screens[h,n]=factor_diagnostics(train,names,3)[-1]
    fund=book(v1,cfg["symbols"])
    base,ablations=candidates(cfg)
    all_rows=[]
    all_summary=[]
    train_log=[]
    predictions={}
    for trial in base+ablations:
        require_space(out,cfg)
        rows,s,oof,logs=validate(trial,prepared,screens,registry,fund,cfg)
        all_rows.extend(rows)
        all_summary.append(s)
        train_log.extend(logs)
        if trial in base:
            predictions[trial.name]=oof
        print(f"{trial.name} validation IC {s['mean_ic']:.6f}",flush=True)
    validation=pd.DataFrame(all_summary)
    contender=validation.loc[~validation.name.str.startswith("ablation")].sort_values("selection_score",ascending=False)
    winner=next(t for t in base if t.name==contender.iloc[0]["name"])
    replicas=[]
    for name in contender.head(2).name:
        trial=next(t for t in base if t.name==name)
        replicas.append(contender.loc[contender.name==name].iloc[0].to_dict())
        for seed in cfg["replicate_seeds"]:
            rows,a,_oof,logs=validate(replace(trial,seed=seed),prepared,screens,registry,fund,cfg)
            all_rows.extend(rows)
            train_log.extend(logs)
            replicas.append(a)
    rep=pd.DataFrame(replicas)
    rep.to_csv(out/"seed_repeats.csv",index=False)
    rep.groupby("name").agg(ic_mean=("mean_ic","mean"),ic_sd=("mean_ic","std"),n=("seed","size")).to_csv(out/"seed_summary.csv")
    pd.DataFrame(all_rows).to_csv(out/"validation_results.csv",index=False)
    pd.DataFrame(train_log).to_csv(out/"training_log.csv",index=False)
    validation.loc[validation.name.str.startswith("ablation")].to_csv(out/"factor_ablation.csv",index=False)
    contender.to_csv(out/"loss_comparison.csv",index=False)
    source,panel=prepared[winner.horizon]
    oof=predictions[winner.name]
    port_rows=[]
    active=[]
    for spec in variants(cfg):
        slopes=[]
        for i in range(1,len(oof)):
            tiled=np.concatenate([oof[j][1] for j in range(i)])
            truth=np.concatenate([oof[j][0]["raw"] for j in range(i)])
            slope=calibrate(tiled,truth)
            view,scores=oof[i]
            perf,_,_=replay(source,panel,view,scores,spec,slope,fund,cfg)
            row=dict(stage="validation",fold=i+1,variant=spec["name"],model=winner.name,calibrated_slope=slope,**perf)
            port_rows.append(row)
            slopes.append(row)
        if sum(r["trades"] for r in slopes)>=10:
            active.append((np.mean([r["net_return"] for r in slopes]),spec))
    rule=max(active,key=lambda r:r[0])[1]
    pd.DataFrame(port_rows).to_csv(out/"portfolio_validation.csv",index=False)
    fixed=dict(validation_main=contender.iloc[0].to_dict(),strategy=rule,
               strategy_validation_mean_net=max(active,key=lambda r:r[0])[0],decision_utc=utc(),
               selection="mean raw IC minus 0.2*fold SD; portfolio by net 6bps with >=10 fills",
               immutable_prior=freeze)
    write_json(out/"selected_research.json",fixed)
    fit_start,edges=calendar(cfg)
    cutoff=edges[-1]
    end=pd.Timestamp(cfg["historical_end"],tz="UTC")
    models=[]
    forecasts=[]
    monthly=[]
    importance=[]
    annual_screen={h:factor_diagnostics(p.view(fit_start,cutoff),names,3)[-1] for h,(_,p) in prepared.items()}
    for trial in base:
        src,pan=prepared[trial.horizon]
        full=pan.view(fit_start,cutoff)
        valid=pan.view(cutoff,end,mature=False)
        cols=columns(pan,registry,annual_screen[trial.horizon],trial.subset,full)
        fitted,prep,x,sec,curve=fit_trial(trial,pan,cols,full,cfg)
        saved=save_model(out,"historical_"+trial.name,fitted,prep)
        if hasattr(fitted,"feature_importance"):
            importance.extend(dict(model=trial.name,factor_name=names[j],gain=value)
                              for j,value in zip(cols,fitted.feature_importance("gain")))
        pred=score_view(fitted,trial.family,x,valid)
        stats,ic,rank=metrics(trial,pred,valid,pan)
        returns,_,_=replay(src,pan,valid,pred,{"rule":"rank"},0,fund,cfg)
        models.append(dict(**asdict(trial),samples=int(np.isfinite(target(full,names,trial.label)).sum()),
                           features=len(cols),fit_seconds=sec,model_file=saved[0].name,
                           feature_names="|".join(names[j] for j in cols),**stats,**returns))
        tag=pd.DataFrame(dict(prediction_time=np.repeat(valid["times"],len(pan.symbols)),symbol=pan.symbols*len(valid["times"]),
                              model=trial.name,score=pred.ravel(),label_raw=valid["raw"].ravel()))
        forecasts.append(tag)
        month=pd.DataFrame(dict(ic=ic,rank_ic=rank),index=valid["times"])
        monthly.append(month.groupby(valid["times"].strftime("%Y-%m")).mean().rename_axis("month").reset_index().assign(model=trial.name,stage="historical"))
        train_log.extend(dict(name=trial.name,seed=trial.seed,fold="historical_annual",epoch=i,loss=l) for i,l in curve)
        if trial==winner:
            raw_oof=np.concatenate([v["raw"] for v,_ in oof])
            score_oof=np.concatenate([s for _,s in oof])
            slope=calibrate(score_oof,raw_oof)
            perf,led,trace,grid=full_cost(src,pan,valid,pred,rule,slope,fund,cfg)
            port_rows.append(dict(stage="historical",variant=rule["name"],model=trial.name,calibrated_slope=slope,**perf))
            trace.to_parquet(out/"historical_target_weights.parquet",index=False)
            led.to_parquet(out/"historical_ledger.parquet",index=False)
            pd.DataFrame(grid).assign(stage="historical",variant=rule["name"],model=trial.name).to_csv(out/"historical_cost_sensitivity.csv",index=False)
        print(f"{trial.name} historical IC {stats['ic']:.6f}",flush=True)
    pd.DataFrame(models).to_csv(out/"model_comparison.csv",index=False)
    pd.DataFrame(importance).to_csv(out/"feature_importance.csv",index=False)
    pd.DataFrame(port_rows).to_csv(out/"portfolio_comparison.csv",index=False)
    pd.DataFrame(train_log).to_csv(out/"training_log.csv",index=False)
    pd.concat(monthly).to_csv(out/"monthly_model_ic.csv",index=False)
    pd.concat(forecasts,ignore_index=True).to_parquet(out/"historical_predictions.parquet",index=False,compression="zstd")
    # Annual retraining at 2026-07-01. Only the already-selected algorithm is fitted.
    t0,annual_edges=calendar(cfg,1)
    _,p_annual=prepared[winner.horizon]
    calibration=[]
    for train,val,a,b in folds(p_annual,cfg,1):
        ranked=factor_diagnostics(train,names,3)[-1]
        cols=columns(p_annual,registry,ranked,winner.subset,train)
        fit,_,x,_,_=fit_trial(winner,p_annual,cols,train,cfg)
        calibration.append((score_view(fit,winner.family,x,val),val["raw"]))
    slope=calibrate(np.concatenate([a for a,_ in calibration]),np.concatenate([b for _,b in calibration]))
    full=p_annual.view(t0,annual_edges[-1])
    selected=factor_diagnostics(full,names,3)[-1]
    cols=columns(p_annual,registry,selected,winner.subset,full)
    fit,prep,_,s,curve=fit_trial(winner,p_annual,cols,full,cfg)
    checkpoints=save_model(out,"fresh_"+winner.name,fit,prep)
    legacy=__import__("json").loads((v2/"frozen_selection.json").read_text())
    cases=[dict(model=asdict(winner),model_key=winner.name,checkpoint=[str(p) for p in checkpoints],
                features=[names[j] for j in cols],slope=slope,rule=rule,
                annual_cutoff=str(annual_edges[-1]),training_seconds=s,
                validation_ic=contender.iloc[0].mean_ic,feature_count=len(cols))]
    for family,loss,rule_name in (("MLP-MSE","MSE","partial_025"),("MASTER","IC","cost_risk_high")):
        item=next(x for x in legacy["configs"] if x["interval"]==240 and x["horizon"]==240 and x["model"]==family and x["loss"]==loss)
        key=f"240-240-{family}-{loss}"
        artifacts=[v2/"models"/f"{key}.pt",v2/"models"/f"{key}.preprocessor.joblib"]
        rule_v2=next(r for r in cfg["frozen_portfolios"] if r["name"]==rule_name)
        cases.append(dict(model=dict(family=family,horizon=240,objective=loss,label="rank"),
                          model_key="V2_"+family+"-"+loss if family=="MASTER" else "V2_MLP-MSE",
                          checkpoint=[str(p) for p in artifacts],features=item["feature_names"],
                          slope=item["calibration_slope"],rule=rule_v2,annual_cutoff=str(cutoff),training_seconds=0))
    for item in cases:
        item["sha256"]=[digest(p) for p in item["checkpoint"]]
    # This file is emitted by research that never opens the fresh store.
    write_json(out/"fresh_frozen_lock.json",dict(cases=cases,utc=utc(),
                fresh_start=cfg["fresh_start"],fresh_end_exclusive=cfg["fresh_end_exclusive"],
                coverage_rule="longest run of days with >=1433 bars per asset and <=12h funding gap, minimum 28 days",
                config_hash=digest(ROOT/"configs/v3.json"),frozen_results=freeze,research_git=commit,
                costs=[0,1,2,4,6,8,10],capital=cfg["capital"],delay_minutes=cfg["delay_minutes"],
                risk_buffer_bps=cfg["risk_buffer_bps"],shared_config_hash=digest(ROOT/"configs/v2.json")))
    # Windows reports peak process working set in bytes.
    peak=None
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[("size",wintypes.DWORD),("faults",wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in
                    ("peak","current","pquota","quota","ppage","page","pagefile","pfile")]
        c=Counters()
        c.size=ctypes.sizeof(c)
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        info=ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes=(wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD)
        if info(kernel.GetCurrentProcess(),ctypes.byref(c),c.size):
            peak=c.peak
    write_json(out/"run_environment.json",dict(device="CPU",torch=torch.__version__,cuda_available=torch.cuda.is_available(),
                peak_ram_bytes=peak,peak_vram_bytes=0,wall_seconds=perf_counter()-started,threads=cfg["threads"],
                disk_free_gib=shutil.disk_usage(out).free/2**30,research_git=commit,
                production_orders=0,demo_orders=0))
    print("V3 RESEARCH FROZEN",flush=True)


if __name__=="__main__":
    with threadpool_limits(limits=2):
        study()
