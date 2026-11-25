"""From frozen scores and audited price panels to offline portfolio cases."""
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parents[1])]

import numpy as np
import pandas as pd

from factor_research.main import freeze
from foundation.dataset import build
from foundation.registry import Registry
from foundation.model import Model as FoundationModel
from model_research.common import custody
from model_research.finalize import ReservedMarket
from model_research.model import Model,Trial
from model_research.study import clock,panel,frozen_rounds
from training.run import funding_inputs
from v2.common import cube
from v3.common import digest,require_space


HIST="OBSERVED_HISTORICAL_TEST"
OCTOBER="OBSERVED_FORWARD_WINDOW"
VALIDATION="INTERNAL_VALIDATION"


def verify(base,factor,models):
    names,proof=custody(base,factor)
    p=models/"artifact_manifest.json"
    data=json.loads(p.read_text())["artifacts"]
    for row in data:
        if digest(models/row["path"])!=row["sha256"]:
            raise ValueError(f"Model Research artifact changed: {row['path']}")
    proof["model_research"]=dict(manifest_sha256=digest(p),artifacts=len(data))
    return names,proof


def settings():
    parent=(ROOT/"../../outputs/crypto_alpha").resolve()
    paths={d:parent/d for d in ("foundation_v1","factor_research_v1","model_research_v1")}
    phase=json.loads((ROOT/"configs/portfolio_research_v1.json").read_text())
    cfg={**json.loads((paths["foundation_v1"]/"resolved_config.json").read_text()),**phase}
    cfg["max_weight"]=cfg["single_asset_cap"]
    out=(ROOT/cfg["output"]).resolve()
    if out.parent!=parent or out.name!="portfolio_research_v1":
        raise ValueError("require separate portfolio research output")
    out.mkdir(parents=True,exist_ok=True)
    require_space(out,cfg)
    return cfg,paths,out


def prepare(cfg,paths,names,out):
    foundation,factor,models=paths.values()
    raw=pd.read_parquet(foundation/"dataset.parquet").reset_index()
    lock=json.loads((models/"fresh_frozen_lock.json").read_text())
    study={**cfg,**lock["training_parameters"]}
    phase_model_path=models/"models"/"mode_a"
    records=[]
    for path in sorted(phase_model_path.iterdir()):
        t=Trial(**json.loads((path/"model.json").read_text())["configuration"])
        records.append(dict(family=t.family,split=HIST,version=t.key,source=path/"targets.parquet",
                            checkpoint=path/"model",factor_set=t.factor_set,kind="Phase3 annual A"))
    fp=foundation/"predictions.parquet"
    frame=pd.read_parquet(fp)
    mlp=frame.loc[frame.model_version.str.contains("MLP-IC")].copy()
    if mlp.model_version.nunique()!=1:
        raise ValueError("expected one Foundation MLP-IC historical checkpoint")
    cp=foundation/"models"/"MLP-IC"
    records.append(dict(family="MLP-IC",split=HIST,version=mlp.model_version.iloc[0],source=fp,
                        checkpoint=cp/"weights",factor_set="FULL_88",kind="Foundation annual A"))
    fp3=models/"fresh_targets.parquet"
    mf=pd.read_parquet(fp3)
    records.append(dict(family=lock["config"]["family"],split=OCTOBER,version=mf.model_version.iloc[0],
                        source=fp3,checkpoint=models/lock["checkpoint"]/"model",
                        factor_set=lock["config"]["factor_set"],kind="Phase3 annual B"))
    receipts=pd.DataFrame([{**r,"predictions_sha256":digest(r["source"]),
                            "checkpoint_sha256":digest(r["checkpoint"])} for r in records])
    receipts.to_csv(out/"prediction_sources.csv",index=False)
    config={**cfg,"start":"2026-10-04","end":"2026-11-01","warmup_days":3,
            "evaluation_horizons_minutes":[30,60,120,240,480,1440]}
    new,_,of,op,*_=build(ReservedMarket(config,models),config,Registry())
    frozen_funding,proxy=funding_inputs(foundation)

    for item in records:
        split=item["split"]
        data=new if split==OCTOBER else raw
        signals=mlp if item["family"]=="MLP-IC" else pd.read_parquet(item["source"])
        yield item,data,signals,(of,op) if split==OCTOBER else (frozen_funding,proxy)

    # Recover asset-level scores in disposable expanding train folds. Cohort and
    # model checkpoint for actual historical/forward replay never change.
    p=panel(raw,names["FULL_88"],60,cfg)
    windows=pd.read_csv(models/"validation_folds.csv")
    edges=list(map(clock,["2025-01-01","2025-03-01","2025-05-01","2025-07-01"]))
    start=clock(cfg["start"])
    oof=[]
    for item in (r for r in records if r["split"]==HIST):
        fam=item["family"]
        if fam=="MLP-IC":
            metadata=FoundationModel.load(cp,cfg)
            columns=[p.factors.index(n) for n in metadata.features]
        else:
            t=Trial(**json.loads((item["checkpoint"].parent/"model.json").read_text())["configuration"])
            columns=names[t.factor_set]
        series=[]
        for fold,(a,b) in enumerate(zip(edges,edges[1:]),1):
            fit=p.view(start,a)
            pred=p.view(a,b)
            if fam=="MLP-IC":
                rounds=5
                take=lambda v:{**v,"x":v["x"][:,:,columns]}
                mod=FoundationModel("MLP-IC",{},cfg,metadata.features).fit(take(fit),rounds=rounds)
                scores=mod.predict(take(pred))
            else:
                prior=windows.loc[windows.trial.eq(t.key)&windows.fold.eq(fold-1),"selected_epoch"]
                rounds=frozen_rounds(float(prior.iloc[0])) if len(prior) else (120 if fam=="LightGBM" else 5)
                mod=Model(t,study,columns).fit(p,fit,rounds=rounds)
                scores=mod.predict(p,pred)
            assert (fit["available"][np.isfinite(fit["raw"])]<=a.value).all()
            df=pd.MultiIndex.from_product([pred["times"],p.symbols],names=["prediction_time","symbol"]).to_frame(index=False)
            df["prediction_score"]=scores.ravel()
            df["calibration_raw_label"]=pred["raw"].ravel()
            df["fold"]=fold
            df["training_cutoff"]=a
            df["validation_end"]=b
            df["training_iterations"]=rounds if fam!="Ridge" else None
            df["model_version"]="calibration_oof_"+fam
            series.append(df)
        combo=pd.concat(series,ignore_index=True)
        oof.append(combo)
        print("CALIBRATION_OOF",fam,len(combo),flush=True)
        yield {**item,"split":VALIDATION,"version":"calibration_oof_"+fam,"kind":"disposable expanding fit"},raw,combo,(frozen_funding,proxy)
    pd.concat(oof,ignore_index=True).to_parquet(out/"calibration_oof_predictions.parquet",index=False,compression="zstd")


def execution_case(data,signals,item,cfg):
    symbols=cfg["symbols"]
    raw=data.loc[data.prediction_time.dt.minute.eq(0)].copy()
    available=pd.DatetimeIndex(np.sort(raw.prediction_time.unique()))
    times=pd.DatetimeIndex(np.sort(signals.prediction_time.unique()))
    if item["split"]==VALIDATION:
        times=times[times+pd.Timedelta(minutes=70)<=clock("2025-07-01")]
    assert len(times)>1 and (times[1:]-times[:-1]==pd.Timedelta(hours=1)).all()
    scores=cube(signals,times,symbols,"prediction_score")
    eligibility=cube(raw,times,symbols,"eligible")
    assert (np.isfinite(scores)<=eligibility).all()
    execution=times+pd.Timedelta(minutes=10)
    execution=execution.append(pd.DatetimeIndex([execution[-1]+pd.Timedelta(hours=1)]))
    mark=cube(raw,times,symbols,"execution_price_proxy")
    final=cube(raw,times[-1:],symbols,"open_end_60")
    returns={h:cube(raw,times,symbols,f"return_{h}") for h in (30,60,120,240,480,1440)}
    if item["split"]==VALIDATION:
        cutoff=signals.drop_duplicates("prediction_time").set_index("prediction_time").validation_end.reindex(times)
        for h, values in returns.items():
            mature=np.asarray(times+pd.Timedelta(minutes=10+h)<=cutoff)
            values[~mature]=np.nan
        returns[60]=cube(signals,times,symbols,"calibration_raw_label")
    return dict(time=times,execution=execution,symbols=symbols,scores=scores,prices=np.vstack([mark,final]),returns=returns,
                all_times=available,risk_returns=cube(raw,available,symbols,"ret_60m"))
