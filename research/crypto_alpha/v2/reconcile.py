"""Frozen V1 evidence and per-timestamp portfolio economics."""
import hashlib
import json
import warnings

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import torch

from models.cpu import net, prediction
from training.metrics import section_ic
from v2.common import book, catalogue, cube, execution_grid, setup, stage_path
from v2.execution import simulate, strength, summarize


def freeze(v1,out):
    source=v1/"artifact_manifest.json"
    manifest=json.loads(source.read_text())
    records=manifest["artifacts"] if isinstance(manifest,dict) else manifest
    # The existing manifest is the read-only boundary; verify declared bytes.
    for item in records:
        path=v1/item["path"]
        with path.open("rb") as stream:
            if hashlib.file_digest(stream,"sha256").hexdigest()!=item["sha256"]:
                raise ValueError(f"V1 input differs from frozen manifest: {path}")
    return hashlib.sha256(source.read_bytes()).hexdigest(),len(records)


def audit(cfg, dates, v1, out):
    catalogue(cfg,dates,out)
    digest,count=freeze(v1,out)
    symbols=cfg["symbols"]
    fund=book(v1,symbols)
    all_rows=[]
    signals=[]
    curve=[]
    traced=[]
    for horizon in (60,240,30,480):
        print(f"reconcile {horizon}m",flush=True)
        stage=stage_path(v1,horizon)
        source=pd.read_parquet(v1/"derived"/f"panel_{horizon}.parquet")
        preds=pd.read_parquet(stage/"predictions.parquet")
        weights=pd.read_parquet(stage/"target_weights.parquet")
        saved=pd.read_csv(stage/"portfolio_returns.csv",parse_dates=["execution_time"])
        saved_summary=pd.read_csv(stage/"model_comparison.csv")
        times=pd.DatetimeIndex(sorted(preds.prediction_time.unique()))
        times_exec,prices=execution_grid(source,times,symbols)
        raw=cube(source,times,symbols,"label_raw")
        source_rows=source[source.prediction_time.isin(times)]
        assert (source_rows.loc[source_rows.eligible,"max_feature_available_time"]<=
                source_rows.loc[source_rows.eligible,"prediction_time"]).all()
        assert (source_rows.entry_time-source_rows.prediction_time==pd.Timedelta(minutes=cfg["delay_minutes"])).all()
        assert (source_rows.label_end_time-source_rows.entry_time==pd.Timedelta(minutes=horizon)).all()
        assert times_exec[:-1].equals(pd.DatetimeIndex(preds.groupby("prediction_time").execution_time.first().reindex(times)))
        label_check=cube(source,times,symbols,"label_exit_price")/cube(source,times,symbols,"label_entry_price")-1
        assert np.allclose(raw,np.where(cube(source,times,symbols,"eligible"),label_check,np.nan),equal_nan=True)
        for (model,method),target_rows in weights.groupby(["model","weight_method"]):
            p=preds.loc[preds.model==model]
            pred=cube(p,times,symbols,"prediction_score")
            w=cube(target_rows,times,symbols,"target_weight")
            target=np.vstack([w,np.zeros(len(symbols))])
            ledger,trace=simulate(times_exec,prices,cfg,fund,target=target,detailed=True)
            ref=saved.loc[(saved.model==model)&(saved.weight_method==method)]
            error={col:float(np.max(np.abs(ledger[col].to_numpy()-ref[col].to_numpy()))) for col in
                   ("equity","gross_pnl","funding","fees","spread","slippage","turnover_usdt","turnover_equity")}
            assert max(error.values())<2e-6,(horizon,model,method,error)
            assert np.allclose(w.sum(axis=1),0,atol=1e-12)
            assert not ((~np.isfinite(pred))&(w!=0)).any()
            zero,_=simulate(times_exec,prices,cfg,fund,target=target,fee=0,spread=0,slip=0)
            simple,_=simulate(times_exec,prices,cfg,fund,target=target,fixed=True)
            summary=summarize(ledger,cfg["capital"])
            pre=np.r_[cfg["capital"],ledger.equity.to_numpy()[:-1]]
            dot=(w*np.nan_to_num(raw)).sum(axis=1)
            opening_return=prices[1:]/prices[:-1]-1
            opening_dot=(w*np.nan_to_num(opening_return)).sum(axis=1)
            prior_trade_equity=pre[:-1]+ledger.gross_pnl.to_numpy()[:-1]+ledger.funding.to_numpy()[:-1]
            label_pnl=dot*prior_trade_equity
            open_pnl=opening_dot*prior_trade_equity
            actual_gross=ledger.gross_pnl.to_numpy()[1:]
            pearson,n=section_ic(pred,raw,cfg["min_assets"])
            rank_ic,_=section_ic(pred,raw,cfg["min_assets"],True)
            positive=w.clip(0)
            negative=(-w).clip(0)
            long_side=(positive*np.nan_to_num(raw)).sum(axis=1)/np.where(positive.sum(axis=1)>0,positive.sum(axis=1),np.nan)
            short_side=(negative*np.nan_to_num(raw)).sum(axis=1)/np.where(negative.sum(axis=1)>0,negative.sum(axis=1),np.nan)
            calibration=strength(pred)
            if method==p.weight_method.iloc[0]:
                record=pd.DataFrame(dict(prediction_time=times,execution_time=times_exec[:-1],interval=horizon,
                        model=model,method=method,ic=pearson,rank_ic=rank_ic,ic_assets=n,
                        score_dispersion=np.nanstd(pred,axis=1),return_dispersion=np.nanstd(raw,axis=1),
                        long_basket_return=long_side,short_basket_return=short_side,long_short_spread=long_side-short_side,
                        weighted_label_return=dot,open_to_open_weighted_return=opening_dot,
                        scaled_label_pnl=label_pnl,execution_proxy_pnl_gap=open_pnl-label_pnl,
                        position_drift_pnl_gap=actual_gross-open_pnl,
                        norm_score_times_return=np.nansum(calibration*(raw-np.nanmean(raw,axis=1,keepdims=True)),axis=1),
                        norm_score_squared=np.nansum(calibration**2,axis=1),
                        traded_assets=(w!=0).sum(axis=1),held_assets=np.stack([d[1]!=0 for d in trace[:-1]]).sum(axis=1),
                        traded_assets_with_label=((w!=0)&np.isfinite(raw)).sum(axis=1),
                        net_pnl=ledger.net_pnl.to_numpy()[1:]))
                signals.append(record)
                bins=np.digitize(calibration,[-1.,-.25,.25,1.])
                available=np.isfinite(pred)&np.isfinite(raw)
                for j in range(5):
                    use=(bins==j)&available
                    ys=raw[use]
                    if len(ys):
                        curve.append(dict(interval=horizon,model=model,score_bin=j,count=len(ys),
                                          mean_rank_strength=float(calibration[use].mean()),mean_label_return=float(ys.mean())))
            if (horizon,model,method) in {(60,"LightGBM","rank"),(60,"MLP-IC","rank"),(240,"LightGBM","rank")}:
                dimensions=np.asarray(trace)
                shape=(len(times_exec),len(symbols))
                columns=["pre_quantity","post_quantity","target_weight","drifted_weight","executed_quantity","mark_price"]
                proof={col:dimensions[:,j,:].ravel() for j,col in enumerate(columns)}
                traced.append(pd.DataFrame(dict(execution_time=np.repeat(times_exec,len(symbols)),
                                                symbol=symbols*shape[0],interval=horizon,model=model,**proof)))
            refrow=saved_summary.loc[(saved_summary.model==model)&(saved_summary.weight_method==method)&(saved_summary.split=="test")].iloc[0]
            assert abs(summary["be_bps"]-refrow.be_bps)<2e-6
            assert abs(summary["net_return"]-refrow.net_return)<2e-9
            assert abs(zero.equity.iloc[-1]/cfg["capital"]-1-refrow.zero_friction_return)<2e-8
            assert abs(simple.equity.iloc[-1]/cfg["capital"]-1-refrow.simple_1x_cumulative_return)<2e-8
            all_rows.append(dict(interval=horizon,model=model,weight_method=method,pass_reconciliation=True,
                max_ledger_error_usdt=max(error.values()),max_equity_error_usdt=error["equity"],
                max_turnover_error_usdt=error["turnover_usdt"],max_funding_error_usdt=error["funding"],
                A_same_fills_cost_removed=summary["fixed_path_zero_return"],
                B_zero_dynamic_replay=zero.equity.iloc[-1]/cfg["capital"]-1,
                C_fixed_notional_simple_6bps=simple.equity.iloc[-1]/cfg["capital"]-1,
                D_dynamic_net_6bps=summary["net_return"],
                weighted_label_return_sum=dot.sum(), actual_equity_scaled_label_pnl=label_pnl.sum(),
                open_clock_proxy_pnl_gap=(open_pnl-label_pnl).sum(),
                drift_or_execution_skip_pnl_gap=(actual_gross-open_pnl).sum(),
                pearson_ic=float(np.nanmean(pearson)),rank_ic=float(np.nanmean(rank_ic)),**summary))
    pd.DataFrame(all_rows).to_csv(out/"v1_reconciliation.csv",index=False)
    pd.concat(signals).to_parquet(out/"v1_signal_economics.parquet",index=False,compression="zstd")
    pd.concat(traced).to_parquet(out/"v1_position_trace.parquet",index=False,compression="zstd")
    pd.DataFrame(curve).to_csv(out/"v1_score_calibration.csv",index=False)
    (out/"v1_frozen_input.json").write_text(json.dumps(dict(manifest_sha256=digest,verified_files=count),indent=2))
    print("V1_RECONCILED",flush=True)


if __name__=="__main__":
    warnings.filterwarnings("ignore",category=DeprecationWarning)
    audit(*setup())
