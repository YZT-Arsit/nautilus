"""Purged equal-budget model research and two actual annual training fits."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
from time import perf_counter

import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from model_research.common import ROOT,custody,settings
from model_research.model import Model,Trial,prepared_label
from factor_research.statistics import block_interval
from foundation.dataset import CrossSectionDataset,task
from foundation.portfolio import replay
from foundation.test_runner import execute
from training.metrics import score_summary
from training.run import code_version,funding_inputs
from v3.common import digest,require_space,write_json


def clock(text):
    return pd.Timestamp(text,tz="UTC")


def panel(raw,names,interval,cfg):
    p0,p1=raw.label_entry_price,raw[f"close_end_{interval}"]
    y=(p1/p0-1).where(p0.gt(0)&p1.gt(0))
    np.testing.assert_allclose(y,raw[f"return_{interval}"],equal_nan=True,rtol=1e-12,atol=1e-12)
    cols=["symbol","prediction_time","eligible","feature_available_time","execution_price_proxy",
          f"open_end_{interval}",f"close_end_{interval}",*names]
    f=raw[cols].assign(**{f"return_{interval}":y})
    return CrossSectionDataset(task(f,interval,interval,cfg["delay_minutes"],cfg["min_assets"],
                                    cfg["label_epsilon"]),cfg["symbols"],names,cfg["min_assets"])


def cutoff_info(v,label,cutoff):
    known=np.isfinite(prepared_label(v,label))
    a=v["available"][known]
    assert len(a)>0 and (a<=cutoff.value).all()
    return pd.Timestamp(a.max(),unit="ns",tz="UTC"),int(known.sum()),int(known.any(axis=1).sum())


def frozen_rounds(value):
    if pd.isna(value):
        return None
    rounds=int(value)
    if rounds<1 or rounds!=value:
        raise ValueError(f"invalid frozen iteration count {value}")
    return rounds


def sampler(panel,cfg):
    edges=list(map(clock,cfg["fold_edges"]))
    first=clock(cfg["start"])
    for i,(a,b) in enumerate(zip(edges,edges[1:]),1):
        yield i,a,b,panel.view(first,a),panel.view(a,b)


def configure():
    methods=[("Ridge","MSE"),("LightGBM","MSE"),("MLP","MSE"),("MLP","IC"),
             ("GRU","MSE"),("GRU","IC"),("StockMixer","StockMixer_ORIGINAL"),("StockMixer","IC"),
             ("MASTER","MASTER_ORIGINAL_MSE"),("MASTER","IC")]
    return [Trial(f,obj,label=label) for f,obj in methods for label in ("rank","raw")]


class Study:
    def __init__(self,cfg,base,out,names):
        self.cfg,self.out,self.base,self.names=cfg,out,base,names
        self.logs,self.folds,self.series,self.summary,self.fails=[],[],[],[],[]
        self.raw=pd.read_parquet(base/"dataset.parquet").reset_index()
        self.panels={}
        self.cache=out/"validation_trials"
        self.cache.mkdir(exist_ok=True)

    def for_trial(self,t):
        if t.interval not in self.panels:
            self.panels[t.interval]=panel(self.raw,self.names["FULL_88"],t.interval,self.cfg)
        return self.panels[t.interval]

    def evaluate(self,t):
        conf=self.cfg
        require_space(self.out,conf)
        loc=self.cache/t.key
        loc.mkdir(exist_ok=True)
        p=self.for_trial(t)
        cols=self.names[t.factor_set]
        frames,logs,folds=[],[],[]
        for n,a,b,train,val in sampler(p,conf):
            path=loc/f"fold_{n}.json"
            if path.is_file():
                item=json.loads(path.read_text())
                fold=item["fold"]
                log=item["logs"]
                sr=pd.read_parquet(loc/f"fold_{n}.parquet")
            else:
                begin=perf_counter()
                mod=Model(t,conf,cols).fit(p,train,val)
                m,sr=mod.evaluate(mod.predict(p,val),val)
                mature,samples,sections=cutoff_info(train,t.label,a)
                validation_maturity,validation_samples, _=cutoff_info(val,t.label,b)
                fold=dict(fold=n,trial=t.key,train_start=conf["start"],train_end=str(a),validation_start=str(a),
                          validation_end=str(b),training_cutoff=str(a),last_matured_label=str(mature),
                          last_validation_label=str(validation_maturity),training_samples=samples,
                          training_sections=sections,validation_samples=validation_samples,seconds=perf_counter()-begin,
                          selected_epoch=mod.rounds,parameter_count=mod.params,**m)
                log=[dict(trial=t.key,fold=n,**x) for x in mod.logs]
                sr=sr.assign(trial=t.key,fold=n,split="INTERNAL_VALIDATION")
                sr.to_parquet(loc/f"fold_{n}.parquet",index=False,compression="zstd")
                write_json(path,dict(fold=fold,logs=log))
            folds.append(fold)
            logs.extend(log)
            frames.append(sr)
        combined=pd.concat(frames,ignore_index=True)
        m=score_summary(combined.ic.to_numpy())
        bounds=block_interval(pd.DatetimeIndex(combined.prediction_time),combined[["ic"]].to_numpy(),
                             conf["block_days"],conf["bootstrap_draws"],t.seed)[0]
        rounds=[x["selected_epoch"] for x in folds if x["selected_epoch"] is not None]
        agg=dict(trial=t.key,**asdict(t),ic=m["mean"],rank_ic=combined.rank_ic.mean(),
                 processed_ic=combined.processed_ic.mean(),icir=m["ir"],positive_ic_ratio=m["positive_ratio"],
                 valid_sections=m["valid_sections"],selection_score=m["mean"],
                 ic_ci_low=bounds[0],ic_ci_high=bounds[1],worst_window_ic=min(x["ic"] for x in folds),
                 sample_sd_window_ic=pd.Series([x["ic"] for x in folds]).std(ddof=1),
                 parameter_count=int(np.median([x["parameter_count"] for x in folds])),
                 best_epoch=int(np.median(rounds)) if rounds else None,
                 best_epoch_by_fold="|".join(str(x["selected_epoch"]) for x in folds),
                 training_seconds=sum(x["seconds"] for x in folds),status="COMPLETE")
        self.folds.extend(folds)
        self.logs.extend(logs)
        self.series.append(combined)
        self.summary.append(agg)
        print(f"VALIDATED {t.key} IC {agg['ic']:.6f}",flush=True)
        return agg

    def sweep(self,trials):
        for t in trials:
            try:
                self.evaluate(t)
            except Exception as error:
                self.fails.append(dict(trial=t.key,**asdict(t),error=repr(error)))
                pd.DataFrame(self.fails).to_csv(self.out/"failed_experiments.csv",index=False)
                print(f"FAILED {t.key} {error!r}",flush=True)

    def annual(self,t,row,mode,cutoff,path,history_end=None):
        p=self.for_trial(t)
        start=clock(self.cfg["start"])
        train=p.view(start,cutoff)
        last,samples,n=cutoff_info(train,t.label,cutoff)
        now=perf_counter()
        model=Model(t,self.cfg,self.names[t.factor_set]).fit(p,train,rounds=frozen_rounds(row["best_epoch"]))
        model.save(path,len(p.symbols))
        recovered=Model.load(path,self.cfg)
        probe=p.view(cutoff-pd.Timedelta(days=2),cutoff)
        np.testing.assert_allclose(model.predict(p,probe),recovered.predict(p,probe),atol=1e-7,rtol=1e-7,equal_nan=True)
        self.logs.extend(dict(trial=t.key,fold=mode,**x) for x in model.logs)
        rec=dict(mode=mode,trial=t.key,starting_history=str(start),training_cutoff=str(cutoff),
                 last_matured_label=str(last),trained_observations=samples,trained_sections=n,validated_reload=True,
                 best_epoch=model.rounds,parameter_count=model.params,seconds=perf_counter()-now,
                 scheduled_end=str(cutoff+pd.DateOffset(years=1)),checkpoint=str(path.relative_to(self.out)))
        print(f"ANNUAL {mode} {t.key} {samples}",flush=True)
        if history_end is None:
            return rec,None,None,None
        test=p.view(cutoff,history_end,mature=False)
        m,s=recovered.evaluate(recovered.predict(p,test),test)
        return rec,m,s,(p,test,recovered.predict(p,test))


def cpu():
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    if os.name=="nt":
        from ctypes import WinDLL,wintypes
        kernel=WinDLL("kernel32")
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetPriorityClass.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)


def runtime_info():
    try:
        report=subprocess.run(["nvidia-smi"],capture_output=True,text=True,timeout=12)
        smi=dict(returncode=report.returncode,text=report.stdout or report.stderr)
    except (OSError,subprocess.TimeoutExpired) as exc:
        smi=dict(returncode=None,text=str(exc))
    return dict(device="CPU",torch=torch.__version__,torch_cuda_available=torch.cuda.is_available(),
                nvidia_smi=smi,peak_vram_bytes=0)


def peak():
    if os.name!="nt":
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    import ctypes as ct
    from ctypes import wintypes
    class Proc(ct.Structure):
        _fields_=[("cb",wintypes.DWORD),("faults",wintypes.DWORD)]+[
            (key,ct.c_size_t) for key in "peak working quota_peak quota nonpaged_peak nonpaged page page_peak".split()]
    c=Proc()
    c.cb=ct.sizeof(c)
    kernel=ct.windll.kernel32
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    f=ct.windll.psapi.GetProcessMemoryInfo
    f.argtypes=(wintypes.HANDLE,ct.POINTER(Proc),wintypes.DWORD)
    return c.peak if f(kernel.GetCurrentProcess(),ct.byref(c),c.cb) else None


def main(cfg,base,factor,out):
    begin=perf_counter()
    names,frozen=custody(base,factor)
    cfg_sha=digest(ROOT/"configs/model_research_v1.json")
    identity=dict(config=cfg_sha,code=code_version(),sources=frozen)
    log=out/"study_identity.json"
    if log.is_file():
        old=json.loads(log.read_text())
        if any(old[k]!=identity[k] for k in ("config","sources")):
            raise ValueError("cached validation uses a different configuration or frozen input")
        # Only the refit iteration conversion changed in this concrete recovery.
        if old["code"]!=identity["code"] and old["code"]!="f60f5265f8c09f229f599bae350e25f4935bcbdb":
            raise ValueError("cached validation comes from a different study implementation")
        identity["validation_commit"]=old.get("validation_commit",old["code"])
        if (out/"fresh_frozen_lock.json").exists():
            print("STUDY_ALREADY_FROZEN",flush=True)
            return
    write_json(log,identity)
    tests=execute(ROOT)
    write_json(out/"tests.json",tests)
    if tests["exit_code"]:
        raise ValueError("model tests failed")
    from model_research.reproduction import reproduce
    reproduce(out)
    write_json(out/"environment.json",runtime_info())
    s=Study(cfg,base,out,names)
    core=configure()
    s.sweep(core)
    trial_map={t.key:t for t in core}
    main_table=pd.DataFrame(s.summary)
    rank=main_table.sort_values(["ic","worst_window_ic"],ascending=False)
    winner_row=rank.iloc[0].to_dict()
    winner=trial_map[winner_row["trial"]]
    neural_row=rank.loc[~rank.family.isin(["LightGBM","Ridge"])].iloc[0].to_dict()
    neural=trial_map[neural_row["trial"]]
    rank_trial=lambda family,objective:next(x for x in core if x.family==family and x.objective==objective and x.label=="rank")
    factors=[replace(t,factor_set=f,scope="factor_set") for t in
              dict.fromkeys([rank_trial("Ridge","MSE"),rank_trial("LightGBM","MSE"),neural,winner])
              for f in ("FULL_88","STABLE_24")]
    arch=[Trial("MASTER","IC",variant=v,scope="architecture") for v in ("no_gate","no_time","no_asset","no_time_no_asset")]
    arch.extend(Trial("StockMixer","IC",variant=v,scope="architecture") for v in ("no_factor_mix","no_time_mix","no_asset_mix"))
    seeds=[replace(neural,scope="seed",seed=seed) for seed in cfg["seeds"][1:]]
    extensions=[replace(t,scope="horizon",interval=h) for t in dict.fromkeys([winner,neural]) for h in (240,1440)]
    s.sweep(factors+arch+seeds+extensions)
    table=pd.DataFrame(s.summary)
    table.to_csv(out/"model_comparison.csv",index=False)
    main_table.to_csv(out/"loss_comparison.csv",index=False)
    factor_keys={t.key for t in factors}
    factor_baselines={replace(t,scope="core",factor_set="NO_DERIVATIVES_83").key for t in factors}
    table.loc[table.trial.isin(factor_keys|factor_baselines)].to_csv(out/"factor_set_comparison.csv",index=False)
    table.loc[table.scope.eq("horizon")].to_csv(out/"horizon_comparison.csv",index=False)
    arch_rows=table.loc[table.scope.eq("architecture")].copy()
    bench=main_table.query("label=='rank' and objective=='IC'").set_index("family").ic
    arch_rows["delta_vs_full"]=arch_rows.ic-arch_rows.family.map(bench)
    table.loc[table.trial.isin([rank_trial("MASTER","IC").key,rank_trial("StockMixer","IC").key,
                              rank_trial("MLP","IC").key,rank_trial("GRU","IC").key])].assign(
                                  delta_vs_full=0.).pipe(lambda x: pd.concat([x,arch_rows])).to_csv(out/"architecture_ablation.csv",index=False)
    seed_table=table.loc[table.trial.isin({t.key for t in seeds}|{neural.key})].copy()
    seed_table["seed_mean_ic"]=seed_table.ic.mean()
    seed_table["seed_sample_sd"]=seed_table.ic.std(ddof=1)
    seed_table.to_csv(out/"seed_results.csv",index=False)
    pd.DataFrame(s.folds).to_csv(out/"validation_folds.csv",index=False)
    pd.DataFrame(s.fails,columns=["trial","family","objective","label","factor_set","variant","interval","seed","scope","error"]).to_csv(out/"failed_experiments.csv",index=False)
    from model_research.reproduction import registry
    registry(out,table,cfg)
    annual=[]; history=[]; series=list(s.series)
    # Commit the selection before loading any historical prediction scores.
    choice=dict(config=asdict(winner),neural=asdict(neural),selected_validation=winner_row,
                factor_sets=names,portfolio="Foundation rank_zscore then L1; 6 bps",
                source=frozen,mode_b_cutoff=cfg["mode_b_cutoff"],configuration_sha256=cfg_sha,device="CPU",
                training_parameters={k:cfg[k] for k in ("lgb_rounds","batch_sections","learning_rate","lookback",
                                                             "max_epochs","patience","min_assets","num_threads")},
                selected_at_utc=datetime.now(timezone.utc).isoformat())
    write_json(out/"selection.json",choice)
    fund,proxy=funding_inputs(base)
    for t in dict.fromkeys([winner,neural,rank_trial("LightGBM","MSE"),rank_trial("Ridge","MSE")]):
        row=rank.loc[rank.trial.eq(t.key)].iloc[0].to_dict()
        destination=out/"models"/"mode_a"/t.key
        rec,m,sig,payload=s.annual(t,row,"A",clock("2025-07-01"),destination,clock("2026-07-01"))
        annual.append(rec)
        p,view,score=payload
        perf,ledger,_,export=replay(score,p,view,cfg,fund,proxy,t.key)
        export.to_parquet(destination/"targets.parquet",index=False)
        ledger.reset_index(drop=True).to_parquet(destination/"ledger.parquet",index=False)
        history.append(dict(trial=t.key,split="OBSERVED_HISTORICAL_TEST",**m,**perf))
        series.append(sig.assign(trial=t.key,split="OBSERVED_HISTORICAL_TEST"))
    pd.DataFrame(history).to_csv(out/"historical_test_results.csv",index=False)
    path=out/"models"/"mode_b"/winner.key
    rec,_,_,_=s.annual(winner,winner_row,"B",clock(cfg["mode_b_cutoff"]),path)
    annual.append(rec)
    pd.DataFrame(annual).to_csv(out/"annual_training.csv",index=False)
    pd.DataFrame(s.logs).to_csv(out/"training_log.csv",index=False)
    info=pd.concat(series,ignore_index=True)
    month=info.assign(month=info.prediction_time.dt.strftime("%Y-%m")).groupby(["trial","split","month"]).agg(
        ic=("ic","mean"),rank_ic=("rank_ic","mean"),processed_ic=("processed_ic","mean"),
        sections=("ic","count")).reset_index()
    month.to_csv(out/"monthly_ic.csv",index=False)
    info.to_parquet(out/"ic_by_section.parquet",index=False,compression="zstd")
    env={**runtime_info(),"peak_ram_bytes":peak(),"seconds":perf_counter()-begin,"disk_free_gib":shutil.disk_usage(out).free/2**30}
    write_json(out/"environment.json",env)
    _,after=custody(base,factor)
    if frozen!=after:
        raise ValueError("frozen sources changed")
    lock=dict(**choice,model_files={x.name:digest(x) for x in path.iterdir()},checkpoint=str(path.relative_to(out)),
              bounds=[cfg["fresh_start"],cfg["fresh_end"]],minimum_complete_days=cfg["fresh_min_days"],
              annual_training_sha256=digest(out/"annual_training.csv"),mode_b_semantics="past two years; fixed thereafter")
    write_json(out/"fresh_frozen_lock.json",lock)
    print("STUDY_COMPLETE",flush=True)


if __name__=="__main__":
    cfg,base,factor,out=settings()
    cpu()
    with threadpool_limits(limits=2):
        main(cfg,base,factor,out)
