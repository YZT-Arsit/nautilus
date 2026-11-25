"""Use pre-test data to select turnover control for saved annual signals."""
import json
from time import perf_counter

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from models.cpu import Preprocessor, fit_model, prediction
from training.metrics import section_ic, score_summary
from training.protocol import Panel
from v2.common import book, cube, execution_grid, setup, stage_path
from v2.execution import calibrate, simulate, summarize


def ic_fields(scores,raw,minimum,normalized=None):
    ic,n=section_ic(scores,raw,minimum)
    rank,_=section_ic(scores,raw,minimum,True)
    p=score_summary(ic)
    return dict(ic=p["mean"],rank_ic=score_summary(rank)["mean"],icir=p["ir"],
                positive_ic_ratio=p["positive_ratio"],valid_sections=p["valid_sections"],
                normalized_label_ic=score_summary(section_ic(scores,normalized,minimum)[0])["mean"]
                if normalized is not None else np.nan)


def research_model(family,reason,features,panel,cfg,start,cutoff,view):
    train=panel.view(start,cutoff)
    indices=[panel.factors.index(k) for k in features]
    y=train["labels"][reason["target"]]
    x=train["x"][:,:,indices]
    preprocess=Preprocessor.fit(x[np.isfinite(y)])
    estimator,_,_=fit_model(family,json.loads(reason["hyperparameters"]),preprocess.transform(x),y,None,cfg,
                            int(reason["rounds"]) if reason["rounds"] is not None else None)
    return np.where(view["eligible"],prediction(estimator,family,preprocess.transform(view["x"][:,:,indices])),np.nan)


def results_for(cfg,dates,v1,out):
    funding=book(v1,cfg["symbols"])
    selection={}
    held={}
    variants=[]
    equity=[]
    cost=[]
    for interval in (60,240):
        stage=stage_path(v1,interval)
        source=pd.read_parquet(v1/"derived"/f"panel_{interval}.parquet")
        factors=pd.read_csv(v1/"factor_registry.csv").factor_name.tolist()
        p=Panel(source,cfg["symbols"],factors,cfg["min_assets"])
        val=p.view(dates["validation_start"],dates["annual_cutoff"],mature=False)
        cal_start=dates["validation_start"]-pd.DateOffset(months=3)
        calibration=p.view(cal_start,dates["validation_start"])
        for family in ("LightGBM","MLP-IC") if interval==60 else ("LightGBM",):
            reason=json.loads((stage/"models"/f"{family}.json").read_text())
            val_score=research_model(family,reason,reason["features"],p,cfg,dates["fit_start"],dates["validation_start"],val)
            calibration_score=research_model(family,reason,reason["features"],p,cfg,dates["fit_start"],cal_start,calibration)
            slope=calibrate(calibration_score,calibration["raw"])
            clock,px=execution_grid(source,val["times"],cfg["symbols"])
            adv=np.expm1(p.matrix("log_quote_volume")[val["selector"]])
            validation=[]
            for spec in cfg["frozen_portfolios"]:
                ledger,_=simulate(clock,px,cfg,funding,scores=val_score,spec=spec,slope=slope,adv=adv)
                row=dict(interval=interval,model=family,variant=spec["name"],rule=spec["rule"],
                         rebalance_minutes=spec.get("rebalance_minutes",interval),label_minutes=interval,
                         split="validation",calibration_slope=slope,**ic_fields(val_score,val["raw"],cfg["min_assets"]),
                         **summarize(ledger,cfg["capital"]))
                row["turnover_limit_breaches"]=_breaches(ledger,spec)
                validation.append(row)
            chosen=max(validation,key=lambda row:row["net_return"])["variant"]
            selection[(interval,family)]=chosen
            variants.extend({**row,"selected":row["variant"]==chosen} for row in validation)
            held[(interval,family)]=(p,source,slope,stage)
    frozen={"selection_criterion":"Max validation net return at 6 bps","qualification":"Historical V1-observed study",
            "choices":[{"interval":i,"model":m,"variant":choice} for (i,m),choice in selection.items()],
            "variants":cfg["frozen_portfolios"]}
    (out/"frozen_portfolio_configs.json").write_text(json.dumps(frozen,indent=2))
    pd.DataFrame(variants).to_csv(out/"portfolio_selection_validation.csv",index=False)
    for (interval,family),(p,source,slope,stage) in held.items():
        print(f"portfolio frozen historical {interval} {family}",flush=True)
        preds=pd.read_parquet(stage/"predictions.parquet")
        preds=preds.loc[preds.model==family]
        times=pd.DatetimeIndex(sorted(preds.prediction_time.unique()))
        score=cube(preds,times,cfg["symbols"],"prediction_score")
        raw=cube(source,times,cfg["symbols"],"label_raw")
        clock,px=execution_grid(source,times,cfg["symbols"])
        adv=np.expm1(cube(source,times,cfg["symbols"],"log_quote_volume"))
        for spec in cfg["frozen_portfolios"]:
            ledger,_=simulate(clock,px,cfg,funding,scores=score,spec=spec,slope=slope,adv=adv)
            stats=summarize(ledger,cfg["capital"])
            selected=selection[(interval,family)]==spec["name"]
            tag=dict(interval=interval,model=family,variant=spec["name"],label_minutes=interval,
                     rebalance_minutes=spec.get("rebalance_minutes",interval))
            variants.append(dict(**tag,rule=spec["rule"],split="frozen_historical",selected=selected,
                                 calibration_slope=slope,**ic_fields(score,raw,cfg["min_assets"]),**stats,
                                 turnover_limit_breaches=_breaches(ledger,spec)))
            equity.append(ledger.assign(**tag))
            for bps in (0,1,2,4,6,8,10):
                cost.append(dict(**tag,bps=bps,basis="FIXED_6BPS_FILLS",
                                 net_return=stats["fixed_path_zero_return"]-stats["turnover_usdt"]*bps/1e4/cfg["capital"]))
                if selected or spec["name"]=="v1_rank":
                    # Preserve the original proportions of fee / spread / slippage.
                    trial,_=simulate(clock,px,cfg,funding,scores=score,spec=spec,slope=slope,adv=adv,
                                     fee=bps*2/3,spread=bps/6,slip=bps/6)
                    cost.append(dict(**tag,bps=bps,basis="DYNAMIC_RERUN",net_return=trial.equity.iloc[-1]/cfg["capital"]-1))
    pd.DataFrame(variants).to_csv(out/"portfolio_comparison.csv",index=False)
    pd.DataFrame(cost).to_csv(out/"cost_sensitivity.csv",index=False)
    pd.concat(equity,ignore_index=True).to_parquet(out/"portfolio_ledger.parquet",index=False)
    print("FROZEN_PORTFOLIO_COMPLETE",flush=True)


def _breaches(ledger,spec):
    if "limit" not in spec:
        return 0
    return int(((ledger.traded_pre_equity>spec["limit"]+1e-10)&(ledger.reason!="TERMINAL")).sum())


if __name__=="__main__":
    with threadpool_limits(limits=2):
        results_for(*setup())
