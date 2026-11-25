"""Calendar windows and validation-driven model selection."""
from dataclasses import dataclass
import json
from itertools import product
from time import perf_counter

import numpy as np
import pandas as pd

from models.cpu import Preprocessor, fit_model, prediction, score
from training.metrics import section_ic, score_summary

TARGETS=["label_raw","label_raw_zscore","label_rank_zscore"]


@dataclass
class Window:
    train_start: pd.Timestamp
    validation_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def annual_windows(start,end,years,validation_months):
    cutoff=start+pd.DateOffset(years=years)
    while cutoff+pd.DateOffset(years=1)<=end:
        yield Window(cutoff-pd.DateOffset(years=years),cutoff-pd.DateOffset(months=validation_months),
                     cutoff,cutoff+pd.DateOffset(years=1))
        cutoff+=pd.DateOffset(years=1)


class Panel:
    def __init__(self, frame, symbols, factors, minimum):
        self.symbols=list(symbols)
        self.factors=list(factors)
        self.min_assets=minimum
        self.times=pd.DatetimeIndex(sorted(frame.prediction_time.unique()))
        keys=pd.MultiIndex.from_product([self.times,symbols],names=["prediction_time","symbol"])
        self.frame=frame.set_index(["prediction_time","symbol"]).reindex(keys)
        self.shape=(len(self.times),len(symbols))
        self.x=self.frame[self.factors].to_numpy().reshape(*self.shape,len(factors))
        self.eligible=self.matrix("eligible").astype(bool)
        self.raw=self.matrix("label_raw")
        self.available=self.frame["label_available_time"].dt.as_unit("ns").astype("int64").to_numpy().reshape(self.shape)
        self.labels={col:self.matrix(col) for col in TARGETS}

    def matrix(self,name):
        return self.frame[name].to_numpy().reshape(self.shape)

    def view(self,start,end,mature=True):
        timeline=(self.times>=start)&(self.times<end)
        label_end=self.frame["label_end_time"].dt.as_unit("ns").astype("int64").to_numpy().reshape(self.shape)
        if not mature:
            # The final signal would create a holding period outside this evaluation year.
            timeline &= (label_end[:,0] <= end.value)
        eligible=self.eligible[timeline]
        raw=self.raw[timeline]
        if mature:
            observed=eligible & np.isfinite(raw) & (self.available[timeline]<=end.value)
        else:
            observed=eligible & np.isfinite(raw)
        raw=np.where(observed,raw,np.nan)
        normalized=self.labels["label_rank_zscore"][timeline]
        informative=np.isfinite(normalized).sum(axis=1)>=self.min_assets
        learnable=observed & informative[:,None]
        return {"x":self.x[timeline],"raw":raw,"eligible":eligible,
                "labels":{k:np.where(learnable,v[timeline],np.nan) for k,v in self.labels.items()},
                "available":self.available[timeline],"times":self.times[timeline],"selector":timeline}


def factor_diagnostics(fit, names, minimum):
    x=fit["x"]
    raw=fit["raw"]
    p,n=section_ic(x.transpose(0,2,1),raw[:,None,:],minimum)
    r,_=section_ic(x.transpose(0,2,1),raw[:,None,:],minimum,True)
    flat=x.reshape(-1,len(names))
    audit=pd.DataFrame(flat,columns=names)
    matrix=np.full((len(names),len(names)),np.nan)
    for i in range(len(names)):
        values,_=section_ic(x[:,:,i,None].transpose(0,2,1),x.transpose(0,2,1),minimum)
        matrix[i]=pd.DataFrame(values).mean(axis=0,skipna=True).to_numpy()
    rows=[]
    rank_rows=[]
    for j,name in enumerate(names):
        stat=score_summary(p[:,j]); rank_stat=score_summary(r[:,j])
        shared=dict(factor_name=name, coverage=float(np.isfinite(flat[:,j]).mean()),
                    mean_assets=float(n[:,j].mean()), too_few_assets=int((n[:,j]<minimum).sum()),
                    constant_or_undefined=int(((n[:,j]>=minimum)&~np.isfinite(p[:,j])).sum()),
                    constant_factor=audit[name].nunique(dropna=True)<=1,
                    abs_p995=audit[name].abs().quantile(.995),abs_max=audit[name].abs().max())
        rows.append({**shared,**stat,"mean_rank_ic":rank_stat["mean"],"rank_icir":rank_stat["ir"]})
        rank_rows.append({**shared,**rank_stat})
    ranks=pd.DataFrame(rows).set_index("factor_name")
    corr=pd.DataFrame(matrix,index=names,columns=names)
    eligible=ranks.index[~ranks.constant_factor & (ranks.coverage>=.40) & ranks["mean"].notna()]
    ordered=ranks.loc[eligible,"mean"].abs().sort_values(ascending=False).index
    selected=[]
    duplicates={}
    for name in ordered:
        if selected and corr.loc[name,selected].abs().max()>=.98:
            duplicates[name]=corr.loc[name,selected].abs().idxmax()
        else:
            selected.append(name)
    ranks["duplicate_of"]=pd.Series(duplicates,dtype="str")
    return ranks,pd.DataFrame(rank_rows).set_index("factor_name"),corr,selected


def tune(panel, window, cfg, out):
    fit=panel.view(window.train_start,window.validation_start)
    val=panel.view(window.validation_start,window.test_start)
    full=panel.view(window.train_start,window.test_start)
    minimum=cfg["min_assets"]
    audits, ranks, corr, usable=factor_diagnostics(fit,panel.factors,minimum)
    audits.to_csv(out/"factor_ic.csv")
    ranks.to_csv(out/"factor_rank_ic.csv")
    corr.to_csv(out/"factor_correlation.csv")
    if not usable:
        raise ValueError("no usable factors")
    features={"deduplicated":usable, "top20":usable[:20]}
    jobs={"Ridge":[{"alpha":a} for a in (.1,1,10)],
          "LightGBM":[{"num_leaves":l,"max_depth":d,"n_estimators":n} for l,d,n in ((15,4,300),(31,6,500))],
          "MLP-MSE":[{}],"MLP-IC":[{}]}
    records,history,winners={},[],{}
    for name in features:
        indices=[panel.factors.index(k) for k in features[name]]
        fit_x=fit["x"][:,:,indices]
        val_x=val["x"][:,:,indices]
        scaler=Preprocessor.fit(fit_x[np.isfinite(fit["labels"]["label_raw"])])
        fit_x=scaler.transform(fit_x)
        val_x=scaler.transform(val_x)
        for family, configurations in jobs.items():
            if name=="top20" and family!="Ridge":
                continue
            for target,params in product(TARGETS,configurations):
                start=perf_counter()
                model,rounds,curve=fit_model(family,params,fit_x,fit["labels"][target],
                                            (val_x,val["raw"],val["eligible"]),cfg)
                result=prediction(model,family,val_x)
                ic=score(result,val["raw"],val["eligible"],minimum)
                trial=len(records)
                rec=dict(trial=trial,model=family,target=target,factor_set=name,
                         feature_count=len(indices),hyperparameters=json.dumps(params),rounds=rounds,
                         validation_ic=ic,training_duration=perf_counter()-start,
                         fit_samples=int(np.isfinite(fit["labels"][target]).sum()),
                         fit_sections=int((np.isfinite(fit["labels"][target]).sum(axis=1)>=minimum).sum()))
                records[trial]=rec
                history.extend(dict(trial=trial,iteration=i,training_loss=loss,validation_ic=metric)
                               for i,loss,metric in curve)
                if family not in winners or ic>winners[family][0]["validation_ic"]:
                    winners[family]=(rec,indices,params,np.where(val["eligible"],result,np.nan))
                print(f"trial {family} {target} {name} raw IC {ic:.6f}",flush=True)
    if any(not np.isfinite(v[0]["validation_ic"]) for v in winners.values()):
        raise ValueError("a model family has no valid validation IC")
    train_result=pd.DataFrame(records.values())
    train_result["selected_within_model"]=train_result.trial.isin([w[0]["trial"] for w in winners.values()])
    train_result.to_csv(out/"training_results.csv",index=False)
    pd.DataFrame(history).to_csv(out/"training_history.csv",index=False)
    return fit,val,full,winners
