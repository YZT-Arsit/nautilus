"""Training-only decisions and time-section diagnostics on the frozen panel."""
import warnings

import numpy as np
import pandas as pd
from scipy.stats import rankdata, kurtosis, skew

from foundation.evaluation import quantile_bins, quantiles
from training.metrics import section_ic, score_summary


def block_interval(times, values, days, draws, seed):
    """Resample contiguous calendar blocks and retain missing ICs as missing."""
    keys = ((times - times[0]) // pd.Timedelta(days=days)).to_numpy()
    sums = pd.DataFrame(np.where(np.isfinite(values), values, 0.)).groupby(keys).sum().to_numpy()
    counts = pd.DataFrame(np.isfinite(values)).groupby(keys).sum().to_numpy()
    weights = np.random.default_rng(seed).multinomial(len(sums), np.full(len(sums), 1/len(sums)), size=draws)
    denom = weights @ counts
    means = np.divide(weights @ sums, denom, out=np.full(denom.shape, np.nan), where=denom > 0)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        return np.nanquantile(means,[.025,.975],axis=0).T


def paired_ic(x, raw, minimum):
    x = x.transpose(0,2,1)
    return (*section_ic(x, raw[:,None,:], minimum), section_ic(x, raw[:,None,:], minimum, True)[0])


def analyze(views, names, cfg, orientation=None):
    anchor = cfg["main_horizon"]
    if orientation is None:
        train = views[anchor]["TRAIN"]
        p, _, _ = paired_ic(train["x"], train["raw"], cfg["min_assets"])
        orientation = np.where(pd.DataFrame(p).mean().to_numpy()<0,-1,1)
    records, monthly, hourly, quantile_rows = [],[],[],[]
    temporal = {}
    for h, splits in views.items():
        for split, view in splits.items():
            x = view["x"]
            p, n, r = paired_ic(x, view["raw"], cfg["min_assets"])
            temporal[h,split] = p
            ci = block_interval(view["times"], p*orientation, cfg["block_days"], cfg["bootstrap_draws"], cfg["seed"])
            months = view["times"].strftime("%Y-%m")
            for i, name in enumerate(names):
                ds = p[:,i]*orientation[i]
                rs = r[:,i]*orientation[i]
                distinct = np.isfinite(x[:,:,i]).sum(axis=1)
                row = dict(factor=name,holding_minutes=h,split=split,train_direction=int(orientation[i]))
                info = pd.DataFrame(dict(ic=p[:,i],rank_ic=r[:,i],directed_ic=ds,assets=n[:,i]),index=view["times"])
                grouped = info.groupby(months).agg(
                    ic=("ic","mean"),rank_ic=("rank_ic","mean"),directed_ic=("directed_ic","mean"),
                    valid_sections=("ic","count"),mean_pairs=("assets","mean"))
                monthly.append(grouped.rename_axis("month").reset_index().assign(**row))
                metrics = score_summary(p[:,i])
                records.append(dict(**row,mean_ic=metrics["mean"],mean_rank_ic=score_summary(r[:,i])["mean"],
                    ic_std=metrics["std"],icir=metrics["ir"],positive_ic_ratio=metrics["positive_ratio"],
                    valid_sections=metrics["valid_sections"],skipped_sections=metrics["skipped_sections"],
                    missing_rate=1-np.isfinite(x[:,:,i]).sum()/max(1,view["eligible"].sum()),
                    mean_valid_assets=n[:,i].mean(),minimum_valid_assets=int(n[:,i].min()),
                    insufficient_sections=int((n[:,i]<cfg["min_assets"]).sum()),
                    asset_count_histogram="|".join(str(int((n[:,i]==k).sum())) for k in range(x.shape[1]+1)),
                    directed_ic=score_summary(ds)["mean"],directed_rank_ic=score_summary(rs)["mean"],
                    directed_ci_low=ci[i,0],directed_ci_high=ci[i,1],
                    positive_month_fraction=float((grouped.directed_ic.dropna()>0).mean()),
                    available_months=int(grouped.directed_ic.notna().sum()),
                    constant_or_undefined=int(((n[:,i]>=cfg["min_assets"])&~np.isfinite(p[:,i])).sum())))
                if h != anchor:
                    continue
                hourly.append(info.rename_axis("prediction_time").reset_index().assign(**row, available_factor_assets=distinct))
                signal = x[:,:,i]*orientation[i]
                y = view["raw"]
                q, turnovers = quantiles(signal,y)
                bins = quantile_bins(signal)
                paired = np.isfinite(y) & (bins>0)
                count = paired.sum(axis=1)
                mean = np.where(paired,y,0).sum(axis=1)/np.maximum(count,1)
                spread = q[3]-q[1]
                complete = np.isfinite(q[1])&np.isfinite(q[2])&np.isfinite(q[3])
                monotone = (q[1]<=q[2])&(q[2]<=q[3])
                spread_month = pd.Series(spread).groupby(months).mean()
                for k in q:
                    size = (bins==k).sum(axis=1)
                    quantile_rows.append(dict(**row,group=k,mean_raw_return=score_summary(q[k]+mean)["mean"],
                        mean_demeaned_return=score_summary(q[k])["mean"],member_turnover=score_summary(turnovers[k])["mean"],
                        eligible_group_sections=int((size>0).sum()),actual_labeled_assets=int(((bins==k)&paired).sum()),
                        mean_group_size=size[size>0].mean() if (size>0).any() else np.nan,
                        missing_group_sections=int((size==0).sum()),valid_return_sections=int(np.isfinite(q[k]).sum()),
                        mean_top_minus_bottom=score_summary(spread)["mean"],
                        monotonic_section_fraction=monotone[complete].mean() if complete.any() else np.nan,
                        positive_spread_month_fraction=(spread_month.dropna()>0).mean(),
                        spread_monthly_sd=spread_month.std(ddof=1)))
    return pd.DataFrame(records),pd.concat(monthly,ignore_index=True),pd.concat(hourly,ignore_index=True),pd.DataFrame(quantile_rows),temporal


def distribution(views, names, cfg):
    train = views["TRAIN"]
    data = []
    for i,name in enumerate(names):
        original = train["x"][:,:,i]
        lo,hi = np.nanquantile(original,[.005,.995])
        for split, view in views.items():
            x = view["x"][:,:,i]
            finite = np.isfinite(x)
            total = np.maximum(1,finite.sum(axis=1))
            m = np.where(finite,x,0).sum(axis=1)/total
            centered = np.where(finite,x-m[:,None],np.nan)
            sd = np.sqrt(np.nansum(centered**2,axis=1)/total)
            z = np.divide(centered,sd[:,None],out=np.full(x.shape,np.nan),where=sd[:,None]>1e-12)
            forms = dict(raw=x,cs_rank=rankdata(x,axis=1,nan_policy="omit"),cs_zscore=z,winsorized=x.clip(lo,hi))
            baseline = score_summary(section_ic(x,view["raw"],cfg["min_assets"])[0])["mean"]
            for transform, values in forms.items():
                sample = values[np.isfinite(values)]
                ic = score_summary(section_ic(values,view["raw"],cfg["min_assets"])[0])["mean"]
                constant = len(sample)>0 and np.min(sample)==np.max(sample)
                quant = np.quantile(sample,[.01,.1,.5,.9,.99]) if len(sample) else [np.nan]*5
                p = dict(zip(["q01","q10","q50","q90","q99"],quant))
                q1,q3 = np.quantile(sample,[.25,.75]) if len(sample) else (np.nan,np.nan)
                iqr = q3-q1
                data.append(dict(factor=name,split=split,transformation=transform,samples=len(sample),
                    mean=sample.mean() if len(sample) else np.nan,std=sample.std(ddof=1) if len(sample)>1 else np.nan,**p,
                    skew=skew(sample) if len(sample)>2 and not constant else np.nan,
                    kurtosis=kurtosis(sample) if len(sample)>3 and not constant else np.nan,
                    missing_rate=1-len(sample)/max(1,view["eligible"].sum()),
                    outlier_rate_iqr3=float(((sample<q1-3*iqr)|(sample>q3+3*iqr)).mean()) if len(sample) else np.nan,
                    raw_outside_training_winsor_limits=((x[finite]<lo)|(x[finite]>hi)).mean() if finite.any() else np.nan,
                    train_clip_low=lo,train_clip_high=hi,constant=constant,
                    constant_sections=int(((finite.sum(axis=1)>=cfg["min_assets"]) & (sd<=1e-12)).sum()),
                    mean_ic=ic,mean_rank_ic=score_summary(section_ic(values,view["raw"],cfg["min_assets"],True)[0])["mean"],
                    incremental_ic=ic-baseline))
    return pd.DataFrame(data)


def decay(table, cfg):
    keys = ["factor","holding_minutes"]
    pivot = table.pivot(index=keys,columns="split",values=["mean_ic","directed_ic","positive_month_fraction"])
    pivot.columns = ["_".join(col).lower() for col in pivot.columns]
    pivot = pivot.reset_index()
    score = pivot.directed_ic_train
    best = pivot.assign(_score=score).dropna(subset=["_score"]).sort_values(
        ["factor","_score","holding_minutes"],ascending=[True,False,True]).drop_duplicates("factor")
    result = pivot.merge(best[["factor","holding_minutes"]].rename(columns={"holding_minutes":"best_training_horizon"}),
                         on="factor",how="left")
    result["sign_consistent_train_validation"] = (np.sign(result.mean_ic_train)==np.sign(result.mean_ic_internal_validation))         & result.mean_ic_train.notna() & result.mean_ic_internal_validation.notna()
    signs = result.groupby("factor").mean_ic_train.transform(lambda vals: max((vals>0).mean(),(vals<0).mean()))
    result["train_sign_consistency_across_horizons"] = signs
    return result
