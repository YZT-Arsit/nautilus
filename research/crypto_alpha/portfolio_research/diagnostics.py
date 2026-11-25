"""Signal distributions, persistence, horizon decay and OOF calibration."""
import numpy as np
import pandas as pd

from foundation.evaluation import quantile_bins
from training.metrics import section_ic
from v3.common import write_json


def signal_statistics(case):
    x=case["scores"]
    f=np.sort(x[np.isfinite(x)])
    q=np.quantile(f,[.001,.01,.05,.25,.5,.75,.95,.99,.999])
    s=np.nanstd(x,axis=1)
    _,valid_pairs=section_ic(x[1:],x[:-1],3)
    rho=section_ic(x[1:],x[:-1],3)[0]
    rank=section_ic(x[1:],x[:-1],3,True)[0]
    bins=quantile_bins(x)
    trend=x-np.nanmean(x,axis=1,keepdims=True)
    known=np.isfinite(trend[1:])&np.isfinite(trend[:-1])
    res=dict(observations=len(f),score_mean=f.mean(),score_sd=f.std(ddof=1),score_min=f[0],
             score_max=f[-1],xs_dispersion_mean=np.nanmean(s),constant_section_rate=np.mean(s<=1e-12),
             missing_rate=1-len(f)/x.size,lag1_score_ic=np.nanmean(rho),lag1_rank_ic=np.nanmean(rank),
             lag1_mean_paired_assets=valid_pairs.mean(),
             centered_sign_persistence=(np.sign(trend[1:])[known]==np.sign(trend[:-1])[known]).mean(),
             outlier_3iqr_rate=np.mean((f<q[3]-3*(q[5]-q[3]))|(f>q[5]+3*(q[5]-q[3]))))
    res.update({f"q{n}":v for n,v in zip(["001","01","05","25","50","75","95","99","999"],q)})
    for k,group in ((1,"bottom"),(3,"top")):
        mask=bins==k
        viable=mask[1:].any(axis=1)&mask[:-1].any(axis=1)
        res[f"{group}_membership_turnover"]=(np.sum(mask[1:]&~mask[:-1],axis=1)[viable]/
                                              mask[1:].sum(axis=1)[viable]).mean()
    decay=[]
    for h,y in case["returns"].items():
        p,_=section_ic(x,y,3)
        r,_=section_ic(x,y,3,True)
        decay.append(dict(label_minutes=h,pearson_ic=np.nanmean(p),rank_ic=np.nanmean(r),
                          valid_sections=int(np.isfinite(p).sum())))
    for lag in (1,2,4,8,24):
        values=section_ic(x[lag:],x[:-lag],3,True)[0]
        res[f"rank_persistence_lag_{lag}"]=np.nanmean(values)
    return res,decay


def strengths_matrix(values,strengths):
    return np.stack([strengths(row)["rank_zscore"] for row in values])


def fit_calibration(case,strength_fn,bins):
    x,y=case["scores"],case["returns"][60]
    rank=strengths_matrix(x,strength_fn)
    good=np.isfinite(x)&np.isfinite(rank)&np.isfinite(y)
    table={}
    for name,score in (("linear",x),("rank_linear",rank)):
        slope,constant=np.linalg.lstsq(np.column_stack([score[good],np.ones(good.sum())]),y[good],rcond=None)[0]
        table[name]=dict(slope=slope,intercept=constant,fit_assets=int(good.sum()))
    edges=np.unique(np.quantile(rank[good],np.linspace(0,1,bins+1)[1:-1]))
    source=np.searchsorted(edges,rank[good],side="left")
    avg=[float(y[good][source==q].mean()) if (source==q).any() else None for q in range(len(edges)+1)]
    table["rank_quantiles"]=dict(edges=edges.tolist(),means=avg,fit_assets=int(good.sum()))
    return table


def calibration_report(case,table,strength_fn):
    raw,y=case["scores"],case["returns"][60]
    rank=strengths_matrix(raw,strength_fn)
    edges=table["rank_quantiles"]["edges"]
    groups=np.searchsorted(edges,rank,side="left")
    known=np.isfinite(raw)&np.isfinite(y)&np.isfinite(rank)
    rows=[]
    for method,s in table.items():
        if "means" in s:
            forecasts=np.array([np.nan if x is None else x for x in s["means"]])[groups]
        else:
            x=raw if method=="linear" else rank
            forecasts=s["intercept"]+s["slope"]*x
        for q in range(len(edges)+1):
            choose=known&(groups==q)&np.isfinite(forecasts)
            if not choose.any():
                continue
            rows.append(dict(method=method,score_quantile=q+1,assets=int(choose.sum()),fit_assets=s["fit_assets"],
                             slope=s.get("slope"),intercept=s.get("intercept"),
                             mean_prediction_return=forecasts[choose].mean(),observed_mean_return=y[choose].mean(),
                             mean_raw_score=raw[choose].mean(),mean_rank_z=rank[choose].mean()))
    return rows
