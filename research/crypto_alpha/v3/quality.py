"""Common-design factor audit across six horizons and market regimes."""
import numpy as np
import pandas as pd

from training.metrics import section_ic,score_summary
from training.protocol import factor_diagnostics
from v3.features import HORIZONS


def quality(frame,registry,cfg,out):
    names=registry.factor_name.tolist()
    stop=pd.Timestamp(cfg["validation_edges"][-1],tz="UTC")
    train=frame.loc[frame.prediction_time<stop]
    times=pd.DatetimeIndex(train.prediction_time.unique()).sort_values()
    idx=pd.MultiIndex.from_product([times,cfg["symbols"]],names=["prediction_time","symbol"])
    wide=train.set_index(["prediction_time","symbol"]).reindex(idx)
    x=wide[names].to_numpy().reshape(len(times),len(cfg["symbols"]),len(names))
    market=wide.rv_1440m.to_numpy().reshape(x.shape[:2])[:,0]
    threshold=np.nanmedian(market[times<pd.Timestamp(cfg["validation_edges"][0],tz="UTC")])
    monthly=[]
    audit=[]
    for h in HORIZONS:
        selected=times+pd.Timedelta(minutes=h+cfg["delay_minutes"])<=stop
        tx=x[selected]
        eligible=wide.eligible.to_numpy().reshape(x.shape[:2])[selected]
        y=np.where(eligible,wide[f"ret_label_{h}"].to_numpy().reshape(x.shape[:2])[selected],np.nan)
        time=times[selected]
        ic,n=section_ic(tx.transpose(0,2,1),y[:,None,:],3)
        ric,_=section_ic(tx.transpose(0,2,1),y[:,None,:],3,True)
        regimes=market[selected]>=threshold
        for j,name in enumerate(names):
            metric=score_summary(ic[:,j])
            s=pd.DataFrame({"ic":ic[:,j],"rank_ic":ric[:,j]},index=time)
            m=s.groupby(time.strftime("%Y-%m")).agg(ic=("ic","mean"),rank_ic=("rank_ic","mean"),sections=("ic","count"))
            monthly.extend(m.rename_axis("month").reset_index().assign(factor_name=name,label_minutes=h).to_dict("records"))
            audit.append(dict(factor_name=name,label_minutes=h,coverage=float(np.isfinite(tx[:,:,j]).mean()),
                              mean_ic=metric["mean"],mean_rank_ic=score_summary(ric[:,j])["mean"],icir=metric["ir"],
                              positive_ratio=metric["positive_ratio"],sections=metric["valid_sections"],
                              monthly_mean=m.ic.mean(),monthly_sd=m.ic.std(),worst_month=m.ic.min(),
                              positive_month_fraction=(m.ic.dropna()>0).mean(),high_btc_vol_ic=score_summary(ic[regimes,j])["mean"],
                              low_btc_vol_ic=score_summary(ic[~regimes,j])["mean"]))
        if h==240:
            old_cut=pd.Timestamp(cfg["validation_edges"][0],tz="UTC")
            take=time+pd.Timedelta(minutes=h+cfg["delay_minutes"])<=old_cut
            _,_,correlation,selected_names=factor_diagnostics({"x":tx[take],"raw":y[take]},names,3)
            correlation.to_csv(out/"factor_correlation.csv")
    quality=pd.DataFrame(audit).merge(registry,on="factor_name",how="left",validate="many_to_one")
    quality["early_train_screen_at_4h"]=quality.factor_name.isin(selected_names)
    quality.to_csv(out/"factor_quality.csv",index=False)
    pd.DataFrame(monthly).to_csv(out/"monthly_factor_ic.csv",index=False)
