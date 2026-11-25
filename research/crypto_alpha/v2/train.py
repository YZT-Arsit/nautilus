"""Validation-first annual model fitting and historical research replay."""
import json
from time import perf_counter

import joblib
import numpy as np
import pandas as pd
import torch

from models.cpu import Preprocessor,fit_model,prediction
from training.protocol import Panel,factor_diagnostics
from v2.common import book,execution_grid
from v2.execution import calibrate,simulate,summarize
from v2.features import horizon_panel
from v2.networks import fit,history,predict
from v2.portfolios import ic_fields

RULES=("v1_rank","partial_025","turnover_020","cost_risk_high","slow_4h","slow_8h")
NEW=("GRU","StockMixer","MASTER")


def phase_view(panel,start,end):
    view=panel.view(start,end,mature=False)
    assert (view["available"][np.isfinite(view["raw"])]<=end.value).all()
    return view


def training_matrix(panel,view):
    y=view["labels"]["label_rank_zscore"]
    return y,np.isfinite(y),int(np.isfinite(y).sum())


def neural_inputs(preprocess,panel,idx):
    masked=np.where(panel.eligible[:,:,None],preprocess.transform(panel.x[:,:,idx]),0.)
    return history(masked,8)


def fit_candidate(family,loss,scaler,panel,columns,train,validation,cfg,rounds=None):
    y,valid,_=training_matrix(panel,train)
    x=scaler.transform(train["x"][:,:,columns])
    if family in NEW:
        all_hist=neural_inputs(scaler,panel,columns)
        evaluation=None if validation is None else (all_hist[validation["selector"]],validation["raw"],validation["eligible"])
        obj,n,curve=fit(family,loss,all_hist[train["selector"]],y,train["eligible"],cfg,evaluation,rounds)
        def forecast(view):
            return np.where(view["eligible"],predict(obj,all_hist[view["selector"]],view["eligible"],cfg["device"]),np.nan)
        return obj,n,curve,forecast
    kind="LightGBM" if family=="LightGBM-V1Factors" else family
    args={"num_leaves":15,"max_depth":4,"n_estimators":240} if kind=="LightGBM" else {"alpha":10}
    eval_m=None if validation is None else (scaler.transform(validation["x"][:,:,columns]),validation["raw"],validation["eligible"])
    obj,n,curve=fit_model(kind,args,x,y,eval_m,cfg,rounds)
    curve=[dict(epoch=i,loss=l,validation_ic=z) for i,l,z in curve]
    def forecast(view):
        xx=scaler.transform(view["x"][:,:,columns])
        return np.where(view["eligible"],prediction(obj,kind,xx),np.nan)
    return obj,n,curve,forecast


def execution_view(source,view,clock_interval,delay,symbols):
    trimmed=source.loc[source.prediction_time.isin(view["times"])].copy()
    trimmed["label_end_time"]=trimmed.prediction_time+pd.Timedelta(minutes=delay+clock_interval)
    return execution_grid(trimmed,view["times"],symbols)


def metric(scores,view,cfg):
    return ic_fields(scores,view["raw"],cfg["min_assets"],view["labels"]["label_rank_zscore"])


def hold_clock(spec,interval):
    return max(interval,spec.get("rebalance_minutes",interval))


def pipeline(frame,features,cfg,dates,v1,out):
    start,valid_at,cutoff,end=(dates[k] for k in ("fit_start","validation_start","annual_cutoff","evaluation_end"))
    symbols=cfg["symbols"]
    funding=book(v1,symbols)
    labels=[(60,60),(60,240),(240,240)]
    diagnostics=[]
    trials=[]
    histories=[]
    planned=[]
    panels={}
    portfolio_validation=[]
    rules=[spec for spec in cfg["frozen_portfolios"] if spec["name"] in RULES]
    for interval,horizon in labels:
        print(f"train candidates {interval}m -> {horizon}m",flush=True)
        source=horizon_panel(frame,interval,horizon,cfg["delay_minutes"],cfg["min_assets"],cfg["label_epsilon"])
        panel=Panel(source,symbols,features,cfg["min_assets"])
        train=panel.view(start,valid_at)
        val=phase_view(panel,valid_at,cutoff)
        clock,px=execution_view(source,val,interval,cfg["delay_minutes"],symbols)
        adv=np.expm1(panel.matrix("log_quote_volume")[val["selector"]])
        audit,_r,corr,usable=factor_diagnostics(train,features,cfg["min_assets"])
        audit["selected"]=audit.index.isin(usable)
        audit["missing_rate"]=1-audit.coverage
        tag={"prediction_minutes":interval,"label_minutes":horizon}
        diagnostics.append(audit.reset_index().assign(**tag))
        corr.to_csv(out/f"factor_corr_{interval}m_{horizon}m.csv")
        default=[panel.factors.index(k) for k in usable]
        legacy=[j for j in default if not panel.factors[j].startswith("v2_")]
        valid=np.isfinite(train["labels"]["label_rank_zscore"])
        panels[(interval,horizon)]=(source,panel)
        jobs=[("Ridge","MSE"),("LightGBM","MSE"),("LightGBM-V1Factors","MSE"),("MLP-MSE","MSE"),("MLP-IC","IC")]
        jobs.extend((family,loss) for family in NEW for loss in ("MSE","IC"))
        for family,loss in jobs:
            t0=perf_counter()
            columns=legacy if family=="LightGBM-V1Factors" else default
            scaler=Preprocessor.fit(train["x"][:,:,columns][valid])
            obj,rounds,curve,forecast=fit_candidate(family,loss,scaler,panel,columns,train,val,cfg)
            select_scores=forecast(val)
            calibrated=calibrate(forecast(train),train["raw"])
            summary=dict(**tag,model=family,loss=loss,rounds=rounds,features=len(columns),
                         train_samples=int(valid.sum()),train_s=perf_counter()-t0,device=cfg["device"])
            val_row={**summary,"split":"validation",**metric(select_scores,val,cfg)}
            trials.append(val_row)
            histories.extend(dict(**tag,model=family,loss_type=loss,**row) for row in curve)
            this=[]
            for spec in rules:
                hold=hold_clock(spec,interval)
                if spec["name"]=="slow_4h" and interval==240:
                    continue
                daily,_=simulate(clock,px,cfg,funding,scores=select_scores,spec=spec,slope=calibrated,adv=adv)
                this.append(dict(**tag,model=family,loss=loss,variant=spec["name"],rebalance_minutes=hold,
                                 calibrated_slope=calibrated,**summarize(daily,cfg["capital"])))
            tradable=[row for row in this if row["trades"]>=10]
            ranked=max(tradable or this,key=lambda row:row["net_return"])
            for row in this:
                row["selected_for_model"]=row["variant"]==ranked["variant"]
            portfolio_validation.extend(this)
            planned.append((interval,horizon,family,loss,rounds,columns,calibrated,ranked["variant"],val_row["ic"]))
            print(f"{interval}/{horizon} {family}-{loss}: val IC {val_row['ic']:.6f}",flush=True)
    comparison=pd.DataFrame(trials)
    validation_portfolios=pd.DataFrame(portfolio_validation)
    selected_ic=comparison.loc[comparison.ic.idxmax()]
    active=validation_portfolios.loc[validation_portfolios.trades>=10]
    selected_cost=active.loc[active.net_return.idxmax()]
    diagnostics=pd.concat(diagnostics,ignore_index=True)
    diagnostics.to_csv(out/"factor_ic.csv",index=False)
    validation_portfolios.to_csv(out/"model_portfolio_selection.csv",index=False)
    comparison.to_csv(out/"training_validation.csv",index=False)
    pd.DataFrame(histories).to_csv(out/"model_training_history.csv",index=False)
    selection=dict(ic_winner=selected_ic.to_dict(),cost_winner=selected_cost.to_dict(),
                   selection_end=str(cutoff),test_status="V1_PREVIOUSLY_OBSERVED_HISTORICAL",
                   configs=[dict(interval=i,horizon=h,model=f,loss=l,rounds=r,
                                 feature_names=[features[j] for j in cols],calibration_slope=s,
                                 selected_variant=b) for i,h,f,l,r,cols,s,b,_ in planned])
    (out/"frozen_selection.json").write_text(json.dumps(selection,indent=2,default=str))
    predictions=[]
    eval_ledger=[]
    test_port=[]
    importance=[]
    for interval,horizon,family,loss,rounds,columns,slope,variant,_ in planned:
        p_source,panel=panels[(interval,horizon)]
        review=phase_view(panel,cutoff,end)
        full=panel.view(start,cutoff)
        y,valid,count=training_matrix(panel,full)
        prep=Preprocessor.fit(full["x"][:,:,columns][valid])
        t0=perf_counter()
        fitted,_,_,forecast=fit_candidate(family,loss,prep,panel,columns,full,None,cfg,rounds)
        fit_time=perf_counter()-t0
        pred=forecast(review)
        model_id=f"{interval}-{horizon}-{family}-{loss}"
        now=pd.DataFrame({"prediction_time":np.repeat(review["times"],len(symbols)),
                          "symbol":symbols*len(review["times"]),"score":pred.ravel(),
                          "label_raw":review["raw"].ravel(),"model_id":model_id})
        predictions.append(now)
        store=out/"models"
        store.mkdir(exist_ok=True)
        if isinstance(fitted,torch.nn.Module):
            torch.save(fitted.state_dict(),store/f"{model_id}.pt")
        elif hasattr(fitted,"save_model"):
            fitted.save_model(str(store/f"{model_id}.txt"))
            importance.extend(dict(model_id=model_id,feature=panel.factors[j],gain=gain)
                              for j,gain in zip(columns,fitted.feature_importance("gain")))
        else:
            joblib.dump(fitted,store/f"{model_id}.joblib")
        joblib.dump(prep,store/f"{model_id}.preprocessor.joblib")
        clocks,px=execution_view(p_source,review,interval,cfg["delay_minutes"],symbols)
        adv=np.expm1(panel.matrix("log_quote_volume")[review["selector"]])
        test_metrics=metric(pred,review,cfg)
        best_cost_case=(interval==selected_cost.prediction_minutes and horizon==selected_cost.label_minutes and
                        family==selected_cost.model and loss==selected_cost.loss)
        for spec in rules:
            if spec["name"] not in {"v1_rank",variant,selected_cost.variant if best_cost_case else ""}:
                continue
            ledger,_=simulate(clocks,px,cfg,funding,scores=pred,spec=spec,slope=slope,adv=adv)
            tag=dict(prediction_minutes=interval,label_minutes=horizon,model=family,loss=loss,variant=spec["name"],
                     rebalance_minutes=hold_clock(spec,interval))
            row=dict(**tag,split="frozen_historical",selected_for_model=spec["name"]==variant,
                     selected_cost_winner=best_cost_case and spec["name"]==selected_cost.variant,
                     refit_s=fit_time,train_samples=count,last_label_time=str(pd.Timestamp(full["available"][valid].max(),unit="ns",tz="UTC")),
                     **test_metrics,**summarize(ledger,cfg["capital"]))
            test_port.append(row)
            eval_ledger.append(ledger.assign(**tag))
            if spec["name"]=="v1_rank":
                trials.append(row)
            if row["selected_cost_winner"]:
                stats=[]
                for bps in (0,1,2,4,6,8,10):
                    restart,_=simulate(clocks,px,cfg,funding,scores=pred,spec=spec,slope=slope,adv=adv,
                                       fee=bps*2/3,spread=bps/6,slip=bps/6)
                    stats.append(dict(**tag,bps=bps,basis="DYNAMIC_RERUN",net_return=restart.equity.iloc[-1]/cfg["capital"]-1))
                    stats.append(dict(**tag,bps=bps,basis="FIXED_6BPS_FILLS",
                                      net_return=row["fixed_path_zero_return"]-row["turnover_usdt"]*bps/1e4/cfg["capital"]))
                pd.DataFrame(stats).to_csv(out/"v2_model_cost_sensitivity.csv",index=False)
    pd.DataFrame(trials).to_csv(out/"model_comparison.csv",index=False)
    pd.DataFrame(test_port).to_csv(out/"model_portfolio_replay.csv",index=False)
    pd.DataFrame(importance).to_csv(out/"feature_importance.csv",index=False)
    pd.concat(eval_ledger,ignore_index=True).to_parquet(out/"model_portfolio_ledger.parquet",index=False)
    pd.concat(predictions,ignore_index=True).to_parquet(out/"predictions.parquet",index=False)
    return selection
