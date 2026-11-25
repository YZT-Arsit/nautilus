"""Cross-sectional IC horizon curves with seven-day block resampling."""
import numpy as np
import pandas as pd

from training.metrics import section_ic,score_summary
from v2.common import cube,stage_path


def block_ci(values,times,seed):
    day=((times-times[0]).total_seconds()//(7*86400)).astype(int)
    good=np.isfinite(values)
    sums=np.bincount(day[good],weights=values[good])
    counts=np.bincount(day[good])
    if counts.size<2:
        return np.nan,np.nan
    rng=np.random.default_rng(seed)
    draws=rng.integers(0,len(counts),size=(1000,len(counts)))
    total=counts[draws].sum(axis=1)
    result=np.divide(sums[draws].sum(axis=1),total,out=np.full(1000,np.nan),where=total>0)
    return tuple(np.nanquantile(result,[.025,.975]))


def measures(score,label,times,seed):
    ic,count=section_ic(score,label,3)
    rank,_=section_ic(score,label,3,True)
    auto,_=section_ic(score[1:],score[:-1],3)
    persist,_=section_ic(score[1:],score[:-1],3,True)
    stats=score_summary(ic)
    rc=score_summary(rank)
    lo,hi=block_ci(ic,times,seed)
    rlo,rhi=block_ci(rank,times,seed)
    summary=dict(ic=stats["mean"],rank_ic=rc["mean"],icir=stats["ir"],rank_icir=rc["ir"],
                 positive_ic_ratio=stats["positive_ratio"],valid_sections=stats["valid_sections"],
                 mean_assets=float(count.mean()),ic_ci95_low=lo,ic_ci95_high=hi,
                 rank_ic_ci95_low=rlo,rank_ic_ci95_high=rhi,signal_autocorrelation=score_summary(auto)["mean"],
                 rank_persistence=score_summary(persist)["mean"])
    series=pd.DataFrame(dict(ic=ic,rank_ic=rank,valid=np.isfinite(ic),prediction_time=times))
    months=series.groupby(times.strftime("%Y-%m")).agg(ic=("ic","mean"),rank_ic=("rank_ic","mean"),valid_sections=("valid","sum"))
    return summary,months.rename_axis("month").reset_index()


def analyze(features,names,cfg,dates,v1,out):
    symbols=cfg["symbols"]
    clock=pd.DatetimeIndex(sorted(features.prediction_time.unique()))
    paths=[]
    months=[]
    economic=[]
    cutoff=dates["annual_cutoff"]
    by_key=features.set_index(["prediction_time","symbol"])
    def matrix(t,columns):
        idx=pd.MultiIndex.from_product([t,symbols],names=["prediction_time","symbol"])
        x=by_key.reindex(idx,columns=columns).to_numpy()
        return x.reshape(len(t),len(symbols),len(columns))
    for interval in (60,240):
        p=pd.read_parquet(stage_path(v1,interval)/"predictions.parquet")
        t=pd.DatetimeIndex(sorted(p.prediction_time.unique()))
        known=matrix(t,[f"ret_label_{h}" for h in cfg["horizons_minutes"]]+[f"immediate_{interval}"])
        for model,observed in p.groupby("model"):
            score=cube(observed,t,symbols,"prediction_score")
            weight=cube(observed,t,symbols,"target_weight")
            for j,h in enumerate(cfg["horizons_minutes"]):
                actual=known[:,:,j]
                metrics,month=measures(score,actual,t,cfg["seed"])
                tag=dict(origin="V1_FROZEN_SCORE",model=model,prediction_minutes=interval,label_minutes=h,
                         split="frozen_historical")
                paths.append({**tag,**metrics})
                months.append(month.assign(**tag))
                if h==interval:
                    immediate=known[:,:,-1]
                    comparable=np.isfinite(actual)&np.isfinite(immediate)&np.isfinite(score)
                    delayed=(weight*np.where(comparable,actual,0)).sum(axis=1)
                    no_delay=(weight*np.where(comparable,immediate,0)).sum(axis=1)
                    economic.append(dict(interval=interval,model=model,delay_minutes=cfg["delay_minutes"],
                                         zero_delay_weighted_return_sum=no_delay.sum(),
                                         actual_delay_weighted_return_sum=delayed.sum(),
                                         delay_effect_sum=(delayed-no_delay).sum(),comparable_pairs=int(comparable.sum())))
    for split,start,end in (
        ("fit",dates["fit_start"],dates["validation_start"]),
        ("validation",dates["validation_start"],cutoff),
        ("frozen_historical",cutoff,dates["evaluation_end"]),
    ):
        for h in cfg["horizons_minutes"]:
            t=clock[(clock>=start)&(clock+pd.Timedelta(minutes=h+cfg["delay_minutes"])<=end)]
            inputs=matrix(t,[*names,f"ret_label_{h}"])
            actual=inputs[:,:,-1]
            for j,name in enumerate(names):
                # All V1 factors and V2 additions remain visible, even if discarded in training.
                score=inputs[:,:,j]
                metrics,month=measures(score,actual,t,cfg["seed"])
                tag=dict(origin="FACTOR",model=name,prediction_minutes=60,label_minutes=h,split=split)
                paths.append({**tag,**metrics})
                months.append(month.assign(**tag))
    pd.DataFrame(paths).to_csv(out/"ic_decay.csv",index=False)
    pd.concat(months,ignore_index=True).to_csv(out/"monthly_ic.csv",index=False)
    pd.DataFrame(economic).to_csv(out/"delay_economics.csv",index=False)
