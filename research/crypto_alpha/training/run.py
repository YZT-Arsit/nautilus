"""CPU-only annual cross-sectional training and offline Nautilus replay."""
import argparse
from dataclasses import fields
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from time import perf_counter

for variable in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"):
    os.environ[variable]="2"

ROOT=Path(__file__).resolve().parents[1]
NAUTILUS=ROOT.parents[1]
sys.path[:0]=[str(ROOT),str(NAUTILUS)]

import joblib
import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from backtest.report import portfolio
from data_engine.events import FundingRateEvent
from models.cpu import Preprocessor, fit_model, prediction
from portfolio.interface import validate_export
from strategy_framework.execution.backtest_report import write_artifact_manifest
from training.metrics import section_ic, score_summary
from training.prepare import configuration
from training.protocol import Panel, annual_windows, tune


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def code_version():
    manifest=ROOT/"code_manifest.json"
    if manifest.is_file():
        info=json.loads(manifest.read_text())
        for file,digest in info["files"].items():
            if fingerprint(ROOT/file)!=digest:
                raise ValueError(f"synchronized code changed on disk: {file}")
        return info["git_commit"]
    return subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()


def funding_inputs(cache):
    frame=pd.read_parquet(cache/"funding_events.parquet")
    lookup={}
    proxies={}
    for symbol, rows in frame.groupby("symbol"):
        lookup[symbol]=[]
        for row in rows.to_dict("records"):
            key=(int(row["event_time_ns"]),symbol)
            proxies[key]=row["bar_mark_proxy"]
            row["mark_price"]=None if pd.isna(row["mark_price"]) else row["mark_price"]
            row["interval_hours"]=None if pd.isna(row["interval_hours"]) else int(row["interval_hours"])
            lookup[symbol].append(FundingRateEvent(**{field.name:row[field.name] for field in fields(FundingRateEvent)}))
    return lookup,proxies


def signal_stats(scores, view, minimum):
    pearson,count=section_ic(scores,view["raw"],minimum)
    rank,_=section_ic(scores,view["raw"],minimum,True)
    norm,_=section_ic(scores,view["labels"]["label_rank_zscore"],minimum)
    series=pd.DataFrame(dict(prediction_time=view["times"],ic=pearson,rank_ic=rank,
                             normalized_label_ic=norm,assets=count))
    p,r,n=(score_summary(a) for a in (pearson,rank,norm))
    metrics=dict(ic=p["mean"],rank_ic=r["mean"],icir=p["ir"],rank_icir=r["ir"],
                 ic_std=p["std"],positive_ic_ratio=p["positive_ratio"],normalized_label_ic=n["mean"],
                 valid_cross_sections=p["valid_sections"], skipped_cross_sections=p["skipped_sections"],
                 too_few_assets=int((count<minimum).sum()),constant_or_undefined=int(((count>=minimum)&~np.isfinite(pearson)).sum()),
                 valid_pairs=int(count.sum()),mean_assets=float(count.mean()))
    return series,metrics


def save_model(out, family, model, prep, columns, version_id, info):
    out.mkdir(exist_ok=True)
    if family=="LightGBM":
        model.save_model(str(out/f"{family}.txt"))
    elif family.startswith("MLP"):
        torch.save(model.state_dict(),out/f"{family}.pt")
    else:
        joblib.dump(model,out/f"{family}.joblib")
    joblib.dump(prep,out/f"{family}.preprocess.joblib")
    (out/f"{family}.json").write_text(json.dumps({"model_version":version_id,"features":columns,**info},
                                                default=str,indent=2),encoding="utf-8")


def complete_frequency(cfg, panel, window, interval, output, fund, proxies, commit):
    fit,val,full,winners=tune(panel,window,cfg,output)
    test=panel.view(window.test_start,window.test_end,mature=False)
    best_family=max(winners,key=lambda family:winners[family][0]["validation_ic"])
    period=dict(train_start=window.train_start,train_end=window.test_start,
                fit_end=window.validation_start,validation_start=window.validation_start,
                validation_end=window.test_start,test_start=window.test_start,test_end=window.test_end,
                training_cutoff=window.test_start)
    models={}
    training_details=[]
    for family,(rec,columns,params,val_scores) in winners.items():
        t0=perf_counter()
        considered=full["labels"][rec["target"]]
        training=np.isfinite(considered)
        full_x=full["x"][:,:,columns]
        scaler=Preprocessor.fit(full_x[training])
        rounds=int(rec["rounds"]) if rec["rounds"] is not None else None
        model,_,refit_curve=fit_model(family,params,scaler.transform(full_x),considered,None,cfg,rounds)
        training_duration=perf_counter()-t0
        model_info={**rec,**period,"refit_duration":training_duration,
                    "last_label_available_time":pd.Timestamp(full["available"][training].max(),unit="ns",tz="UTC"),
                    "training_samples":int(training.sum()),"training_cross_sections":int((training.sum(axis=1)>=cfg["min_assets"]).sum()),
                    "test_label_samples":int(np.isfinite(test["raw"]).sum()),
                    "test_label_sections":int((np.isfinite(test["raw"]).sum(axis=1)>=cfg["min_assets"]).sum())}
        version_id=f"{commit[:10]}-{interval}m-{window.test_start:%Y%m%d}-{family}-{rec['trial']}"
        features=[panel.factors[i] for i in columns]
        save_model(output/"models",family,model,scaler,features,version_id,model_info)
        if refit_curve:
            pd.DataFrame(refit_curve,columns=["epoch","training_loss","validation_ic"]).to_csv(
                output/f"refit_{family}.csv",index=False)
        scores=prediction(model,family,scaler.transform(test["x"][:,:,columns]))
        models[family]=(version_id,val_scores,np.where(test["eligible"],scores,np.nan),"rank")
        training_details.append({"model_version":version_id,**model_info})
    for name,factor,sign,kind in (("Equal long/short","ret_240m",1,"equal"),
                                  ("Momentum","ret_240m",1,"rank"),("Reversal","ret_5m",-1,"rank")):
        if factor in panel.frame:
            values=panel.matrix(factor)*sign
            models[name]=(f"{commit[:10]}-{interval}m-{name.replace('/','-')}",
                          np.where(val["eligible"],values[val["selector"]],np.nan),
                          np.where(test["eligible"],values[test["selector"]],np.nan),kind)
    pd.DataFrame(training_details).to_csv(output/"annual_training.csv",index=False)
    chosen_id, chosen_val, chosen_test, _=models[best_family]
    comparisons, curves, trades, days, sensitivities, predictions, targets=[],[],[],[],[],[],[]
    for model_name,(model_id,valid_scores,test_scores,kind) in models.items():
        for split,view,scores in (("validation",val,valid_scores),("test",test,test_scores)):
            series,stat=signal_stats(scores,view,cfg["min_assets"])
            eligibility_by_time = (scores.shape[1] - np.isfinite(scores).sum(axis=1))
            series["unscored_assets"]=eligibility_by_time
            curves.append(series.assign(model=model_name,split=split))
            active_view=view
            active_scores=scores
            if split=="validation":
                # Remove the purely time-defined tail from the validation replay.
                sides=panel.view(window.validation_start,window.test_start,mature=False)
                active_view=sides
                active_scores=scores[:len(sides["times"])]
            economic,trade,daily,cost,relative,wt=portfolio(
                active_scores,panel,active_view,kind,cfg,fund,proxies,economics=(split=="test"))
            comparisons.append(dict(model=model_name,model_version=model_id,split=split,weight_method=kind,
                                    best_validation_model=model_name==best_family,**stat,**economic))
            if split=="test":
                trades.append(trade.assign(model=model_name,weight_method=kind))
                days.append(daily.assign(model=model_name,weight_method=kind))
                sensitivities.append(cost.assign(model=model_name,weight_method=kind))
                keys=pd.MultiIndex.from_product([view["times"],panel.symbols],names=["prediction_time","symbol"])
                data=keys.to_frame(index=False)
                data["execution_time"]=data.prediction_time+pd.Timedelta(minutes=cfg["delay_minutes"])
                data["model"]=model_name
                data["model_version"]=model_id
                data["prediction_score"]=scores.ravel()
                data["relative_strength"]=relative.ravel()
                data["target_weight"]=wt.ravel()
                data["weight_method"]=kind
                predictions.append(data.copy())
                targets.append(data)
        print(f"replay {interval}m {model_name} complete",flush=True)
    for kind in ("raw","equal"):
        economic,trade,daily,cost,relative,wt=portfolio(chosen_test,panel,test,kind,cfg,fund,proxies)
        comparison=next(r for r in comparisons if r["model"]==best_family and r["split"]=="test").copy()
        comparisons.append({**comparison,"weight_method":kind,**economic})
        trades.append(trade.assign(model=best_family,weight_method=kind))
        days.append(daily.assign(model=best_family,weight_method=kind))
        sensitivities.append(cost.assign(model=best_family,weight_method=kind))
        template=next(t for t in predictions if t.model_version.iloc[0]==chosen_id).copy()
        template["relative_strength"]=relative.ravel()
        template["target_weight"]=wt.ravel()
        template["weight_method"]=kind
        targets.append(template)
    model_comparison=pd.DataFrame(comparisons)
    model_comparison.to_csv(output/"model_comparison.csv",index=False)
    ic=pd.concat(curves,ignore_index=True)
    ic.to_csv(output/"prediction_ic.csv",index=False)
    pnl=pd.concat(trades,ignore_index=True)
    pnl.to_csv(output/"portfolio_returns.csv",index=False)
    turnover=pd.concat(days).rename_axis("date").reset_index()
    turnover.to_csv(output/"turnover.csv",index=False)
    costs=pd.concat(sensitivities,ignore_index=True)
    costs.to_csv(output/"cost_sensitivity.csv",index=False)
    pd.concat(predictions,ignore_index=True).to_parquet(output/"predictions.parquet",index=False)
    target_frame=pd.concat(targets,ignore_index=True)
    target_frame.to_parquet(output/"target_weights.parquet",index=False)
    chosen_targets=target_frame.loc[(target_frame.model_version==chosen_id)&(target_frame.weight_method=="rank")]
    validate_export(chosen_targets,cfg["delay_minutes"]).to_parquet(output/"nautilus_targets.parquet",index=False)
    from training.visual import summary_plot
    summary_plot(output,interval,best_family,ic,pnl,turnover,costs,cfg["capital"])
    chosen=model_comparison.loc[(model_comparison.model==best_family)&(model_comparison.weight_method=="rank")]
    rec=next(r for r in training_details if r["model"]==best_family)
    return [{**v,"interval_minutes":interval,"training_duration":rec["refit_duration"],"test_start":window.test_start}
            for v in chosen.to_dict("records")]


def check_disk(out):
    reserve = float(os.environ.get("CRYPTO_ALPHA_FREE_SPACE_GIB","0"))
    remaining = shutil.disk_usage(out).free / (1024**3)
    if remaining < reserve:
        raise OSError(f"output disk has {remaining:.2f} GiB free, below the {reserve:.2f} GiB reserve")
    return remaining


def run(cfg,out,intervals,resume=False):
    wall=perf_counter()
    if os.name=="nt":
        from ctypes import WinDLL, WinError, get_last_error, wintypes
        kernel=WinDLL("kernel32",use_last_error=True)
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetPriorityClass.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        kernel.GetPriorityClass.argtypes=(wintypes.HANDLE,)
        handle=kernel.GetCurrentProcess()
        if not kernel.SetPriorityClass(handle,0x00004000):
            raise WinError(get_last_error())
        print(f"Windows process priority class: {kernel.GetPriorityClass(handle)}",flush=True)
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    initial_free_gib=check_disk(out)
    cache=out/"derived"
    if json.loads((cache/"prepared.json").read_text())["config"]!=cfg:
        raise ValueError("prepared data and run configuration differ")
    commit=code_version()
    metadata=dict(git_commit=commit,python=sys.version,machine=platform.node(),platform=platform.platform(),
                  deps={p:version(p) for p in ("numpy","pandas","pyarrow","scipy","scikit-learn","lightgbm","torch","matplotlib")},
                  torch_cuda_build=torch.version.cuda,production_orders=0,demo_orders=0,seed=cfg["seed"],threads=cfg["threads"],
                  initial_output_disk_free_gib=initial_free_gib)
    env_path=out/"run_environment.json"
    previous_env=json.loads(env_path.read_text()) if resume and env_path.is_file() else {}
    if previous_env:
        metadata["resumed_from_commit"]=previous_env.get("git_commit")
        metadata["prior_wall_seconds"]=previous_env.get("wall_seconds")
    same_code=previous_env.get("git_commit")==commit
    env_path.write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    (out/"configs").mkdir(exist_ok=True)
    (out/"configs"/"v1.json").write_text(json.dumps(cfg,indent=2),encoding="utf-8")
    start,end=(pd.Timestamp(cfg[p],tz="UTC") for p in ("start","end"))
    windows=list(annual_windows(start,end,cfg["fit_years"],cfg["validation_months"]))
    if not windows:
        raise ValueError("need fit_years plus one full out-of-sample year")
    funding,proxies=funding_inputs(cache)
    registry=pd.read_csv(out/"factor_registry.csv")
    enabled=registry.loc[registry.enabled,"factor_name"].tolist()
    comp_path=out/"frequency_comparison.csv"
    comparisons=[]
    if resume and same_code and comp_path.is_file():
        existing=pd.read_csv(comp_path,parse_dates=["test_start"])
        comparisons=[row for row in existing.to_dict("records") if row["interval_minutes"] in intervals]
    finished={(int(row["interval_minutes"]),row["test_start"]) for row in comparisons if row["split"]=="test"}
    for interval in intervals:
        panel=Panel(pd.read_parquet(cache/f"panel_{interval}.parquet"),cfg["symbols"],enabled,cfg["min_assets"])
        for window in windows:
            is_primary=(interval==60 and len(windows)==1)
            dest=out if is_primary else out/"frequency"/f"{interval}m"/f"{window.test_start:%Y%m%d}"
            if (interval,window.test_start) in finished and all((dest/file).is_file() for file in
                    ("summary.png","model_comparison.csv","predictions.parquet","nautilus_targets.parquet")):
                print(f"RESUME {interval}m {window.test_start}: preserved completed fold",flush=True)
                continue
            comparisons=[row for row in comparisons if
                         (row["interval_minutes"],row["test_start"])!=(interval,window.test_start)]
            check_disk(out)
            dest.mkdir(parents=True,exist_ok=True)
            print(f"START {interval}m {window.test_start}",flush=True)
            comparisons.extend(complete_frequency(cfg,panel,window,interval,dest,funding,proxies,commit))
            combined=pd.DataFrame(comparisons)
            val=combined.loc[combined.split=="validation"]
            selected=val.groupby("test_start").ic.idxmax()
            keys=pd.MultiIndex.from_frame(val.loc[selected,["test_start","interval_minutes"]])
            combined["selected_by_validation_ic"]=pd.MultiIndex.from_frame(combined[["test_start","interval_minutes"]]).isin(keys)
            combined.to_csv(out/"frequency_comparison.csv",index=False)
    metadata["wall_seconds"]=perf_counter()-wall
    metadata["final_output_disk_free_gib"]=check_disk(out)
    env_path.write_text(json.dumps(metadata,indent=2),encoding="utf-8")
    write_artifact_manifest(out,"crypto_alpha_v1")
    print("TRAINING_COMPLETE",flush=True)


if __name__=="__main__":
    args=argparse.ArgumentParser()
    args.add_argument("--config",default=ROOT/"configs/v1.json")
    args.add_argument("--output")
    args.add_argument("--intervals",nargs="+",type=int)
    args.add_argument("--resume",action="store_true")
    parsed=args.parse_args()
    cfg,_,out=configuration(parsed.config,parsed.output)
    with threadpool_limits(limits=cfg["threads"]):
        run(cfg,out,parsed.intervals or cfg["interval_minutes"],parsed.resume)
