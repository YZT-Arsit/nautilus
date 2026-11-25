"""Freeze earlier results and replay replaceable policies without refitting annual models."""
import json
import os
from time import perf_counter

import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from portfolio_research.sources import HIST,OCTOBER,VALIDATION,ROOT,prepare,execution_case,settings,verify
from portfolio_research.rules import prediction_to_strength,risk_at_times,strength_to_weights,audit_weights
from portfolio_research.diagnostics import signal_statistics,fit_calibration,calibration_report
from portfolio_research.engine import evaluate_portfolio,bridge,observation_stats
from foundation.test_runner import execute
from model_research.study import cpu
from strategy_framework.execution.backtest_report import write_artifact_manifest
from training.run import code_version
from v3.common import digest,require_space,write_json


def funding_book(case,events,proxy):
    start,end=case["execution"][[0,-1]]
    clean,packed={},[]
    for j,s in enumerate(case["symbols"]):
        batch={}
        for e in events.get(s,[]):
            if start.value<=e.event_time_ns<=end.value:
                if e.event_time_ns in batch and batch[e.event_time_ns]!=e:
                    raise ValueError("conflicting same-clock settlement")
                batch[e.event_time_ns]=e
        clean[s]=list(batch.values())
        for e in clean[s]:
            fallback=proxy.get((e.event_time_ns,s),np.nan)
            packed.append((e.event_time_ns,j,e.funding_rate,e.mark_price if e.mark_price is not None else fallback,
                           int(e.mark_price is None)))
    return sorted(packed),clean


def risk_report(case,pack,cov,beta,ledger):
    q=pack[:-1,1]
    marks=pack[:-1,5]
    vals=q*marks
    actual=vals/ledger.equity.to_numpy()[:-1,None]
    targets=pack[:-1,2]
    beta_path=np.where(np.any(~np.isfinite(beta)&(q!=0),axis=1),np.nan,
                       (np.nan_to_num(beta)*actual).sum(axis=1))
    target_beta=np.where(np.any(~np.isfinite(beta)&(targets!=0),axis=1),np.nan,
                         (np.nan_to_num(beta)*targets).sum(axis=1))
    valid=np.isfinite(cov).all((1,2))
    expected=np.full(len(cov),np.nan)
    expected[valid]=np.sqrt(np.maximum(0,np.einsum("ij,ijk,ik->i",targets[valid],cov[valid],targets[valid])))
    short=-actual.clip(max=0)
    long=actual.clip(0)
    denom_l=long.sum(axis=1)
    denom_s=short.sum(axis=1)
    hhi=lambda z,denom:np.divide((z*z).sum(1),denom**2,out=np.zeros_like(denom),where=denom>0)
    return dict(mean_actual_gross=np.abs(actual).sum(axis=1).mean(),max_abs_actual_net=np.abs(actual.sum(axis=1)).max(),
                avg_btc_beta=np.nanmean(beta_path),avg_abs_btc_beta=np.nanmean(np.abs(beta_path)),
                avg_target_beta=np.nanmean(target_beta),max_abs_btc_beta=np.nanmax(np.abs(beta_path)),
                max_actual_asset_weight=np.abs(actual).max(),
                max_target_asset_weight=np.abs(targets).max(),long_hhi=hhi(long,denom_l).mean(),short_hhi=hhi(short,denom_s).mean(),
                target_mean_vol_hourly=np.nanmean(expected),
                actual_vol_hourly=ledger.equity.pct_change().std(ddof=1),
                mean_long=denom_l.mean(),mean_short=-denom_s.mean(),risk_valid_sections=int(valid.sum()))


def turnover_report(case,pack,ledger,cost):
    after,before=pack[:,1],pack[:,0]
    prices=pack[:,5]*(1+np.sign(pack[:,4])*cost/3/1e4)
    lon=np.abs(np.maximum(after,0)-np.maximum(before,0))*prices
    sho=np.abs(np.minimum(after,0)-np.minimum(before,0))*prices
    np.testing.assert_allclose((lon+sho).sum(1),ledger.turnover_usdt,atol=1e-7)
    targets=pack[:,2]
    drift=pack[:,3]
    target_delta=np.abs(np.diff(np.vstack([np.zeros_like(targets[:1]),targets]),axis=0)).sum(1)
    result=pd.DataFrame(dict(day=ledger.execution_time.dt.floor("D"),target_l1_change=target_delta,
                             target_vs_drift_l1=np.abs(targets-drift).sum(1),long_turnover_usdt=lon.sum(1),
                             short_turnover_usdt=sho.sum(1),executed_turnover_usdt=ledger.turnover_usdt,
                             executed_equity_turnover=ledger.turnover_equity,holding_hours=ledger.duration_hours,
                             closed_episodes=ledger.closed_positions))
    return result.groupby("day",as_index=False).sum()


def fixed_path_cost(pack,cost):
    quantities=pack[:,4]
    adv=cost/3/1e4
    price=pack[:,5]
    slip=np.abs(quantities*price).sum()*adv
    total=np.abs(quantities)*price*(1+np.sign(quantities)*adv)
    return total.sum()*(cost*2/3)/1e4+slip


def export_trace(out,name,case,pack,ledger,cfg):
    ids=pd.MultiIndex.from_product([case["execution"],case["symbols"]],names=["execution_time","symbol"]).to_frame(index=False)
    ids["prediction_time"]=ids.execution_time-pd.Timedelta(minutes=10)
    ids.loc[ids.execution_time.eq(case["execution"][-1]),"prediction_time"]=pd.NaT
    ids["terminal"]=ids.prediction_time.isna()
    vals={"previous_quantity":pack[:,0],"actual_quantity":pack[:,1],"target_weight":pack[:,2],
          "pretrade_drift_weight":pack[:,3],"filled_quantity_delta":pack[:,4],"account_mark":pack[:,5]}
    for col,z in vals.items():
        ids[col]=z.ravel()
    qty_base=ledger.equity+ledger.fees+ledger.spread+ledger.slippage
    when=case["prices"]
    ids["execution_price_proxy"]=when.ravel()
    requested=np.divide(pack[:,2]*qty_base.to_numpy()[:,None],when,
                        out=np.full(when.shape,np.nan),where=np.isfinite(when)&(when>0))
    requested[(~np.isfinite(when))&(pack[:,2]==0)]=0
    ids["requested_target_qty"]=requested.ravel()
    ids["model_version"]=name[1]
    ids["split"]=name[0]
    ids["method"]=name[2]
    return ids


def run(cfg,paths,out):
    started=perf_counter()
    names,frozen=verify(*paths.values())
    write_json(out/"freeze.json",frozen)
    source=ROOT/"configs/portfolio_research_v1.json"
    version={p.name:digest(p) for p in sorted((ROOT/"portfolio_research").glob("*.py")) if p.name!="verify.py"}
    identity=dict(config=digest(source),code=version,sources=frozen)
    write_json(out/"protocol_lock.json",dict(config_sha256=digest(source),methods=cfg["methods"],
        extensions=cfg["extensions"],inputs=frozen,costs=cfg["cost_grid_bps"],
        validation="OOF calibration only; no portfolio method selected by outcomes",research_window_status={
          HIST:"previously observed",OCTOBER:"previously observed",VALIDATION:"internal train-year replay"},
        forward_test="reserved for future; no market download"))
    test=execute(ROOT)
    write_json(out/"tests.json",test)
    if test["exit_code"]:
        raise ValueError("unit tests failed")
    specs={x["name"]:x for x in json.loads((ROOT/"configs/v2.json").read_text())["frozen_portfolios"]}
    acc={k:[] for k in ("score","decay","strength","weights","bridge","risk","daily","portfolio","cost","calibration")}
    families={}
    risk_cache={}
    dirs=out/"accounts"
    dirs.mkdir(exist_ok=True)
    for item,data,signals,funding in prepare(cfg,paths,names,out):
        require_space(out,cfg)
        split,family,model_id=(item[k] for k in ("split","family","version"))
        key=dict(split=split,model=family,model_version=model_id,factor_set=item["factor_set"])
        case=execution_case(data,signals,item,cfg)
        sex,lines=signal_statistics(case)
        acc["score"].append(dict(**key,**sex))
        acc["decay"].extend(dict(**key,**r) for r in lines)
        if split==VALIDATION:
            families[family]=fit_calibration(case,prediction_to_strength,cfg["calibration_bins"])
        packed,clean=funding_book(case,*funding)
        date_key="october" if split==OCTOBER else "historic"
        if date_key not in risk_cache:
            risk_cache[date_key]=risk_at_times(case["risk_returns"],cfg)
        idx=case["all_times"].get_indexer(case["time"])
        assert (idx>=0).all()
        covariance,beta=(m[idx] for m in risk_cache[date_key])
        if split!=VALIDATION:
            for h,m in enumerate(case["symbols"]):
                for k,n in enumerate(case["symbols"]):
                    x=covariance[:,h,k]
                    if np.isfinite(x).any():
                        acc.setdefault("covariance",[]).append(dict(**key,asset_1=m,asset_2=n,mean_cov=np.nanmean(x),
                                                                    sections=int(np.isfinite(x).sum())))
        a=dirs/split/family
        a.mkdir(parents=True,exist_ok=True)
        score=np.array(case["scores"])
        relative=[prediction_to_strength(row,cfg["min_assets"]) for row in score]
        strength=pd.DataFrame(index=pd.MultiIndex.from_product([case["time"],case["symbols"]],
                                                              names=["prediction_time","symbol"]))
        for method in relative[0]:
            x=np.stack([r[method] for r in relative])
            strength[method]=x.ravel()
            acc["strength"].append(dict(**key,method=method,finite_scores=int(np.isfinite(x).sum()),
                                        mean=np.nanmean(x),sample_sd=np.nanstd(x,ddof=1),
                                        min=np.nanmin(x),max=np.nanmax(x),min_assets=cfg["min_assets"]))
        strength.to_parquet(a/"relative_strength.parquet",compression="zstd")
        pd.DataFrame(dict(score=score.ravel(),raw_label=case["returns"][60].ravel()),
                     index=strength.index).to_parquet(a/"calibration_input.parquet",compression="zstd")
        with open(a/"case.json","w",encoding="utf-8") as f:
            json.dump(key,f,indent=2)
        for method in cfg["methods"]+cfg["extensions"]:
            info=dict(**key,method=method,policy_type="extension_from_V2" if method in specs else "standard_comparison")
            output=a/method
            output.mkdir(exist_ok=True)
            receipt=output/"account_receipt.json"
            if receipt.exists():
                stored=json.loads(receipt.read_text())
                if stored["identity"]==identity:
                    for field,part in stored["details"].items():
                        acc[field].extend(part)
                    acc["daily"].append(pd.read_parquet(output/"daily_turnover.parquet"))
                    print(f"REUSED {split} {family} {method}",flush=True)
                    continue
            static=np.stack([strength_to_weights(row,method,cfg,cov) for row,cov in zip(score,covariance)]
                            +[np.zeros(len(case["symbols"]))]) if method in cfg["methods"] else None
            spec=specs.get(method)
            main=cfg["primary_cost_bps"]
            perf,l,trace=evaluate_portfolio(case,spec,static,cfg,packed,clean,funding[1],cost=main,proof=True)
            bridge_rows=bridge(case,l,trace,cfg["capital"])
            bstats=observation_stats(bridge_rows)
            gross=audit_weights(trace[:-1,2],np.isfinite(score),cfg)
            max_abs_net=np.abs(trace[:-1,2].sum(1)).max()
            turnover=turnover_report(case,trace,l,main)
            holdings=risk_report(case,trace,covariance,beta,l)
            export_trace(out,(split,model_id,method),case,trace,l,cfg).to_parquet(output/"target_and_actual_positions.parquet",
                                                                                index=False,compression="zstd")
            l.to_parquet(output/"audited_ledger.parquet",index=False,compression="zstd")
            bridge_rows.to_parquet(output/"ic_return_by_section.parquet",index=False,compression="zstd")
            acc["weights"].append(dict(**info,mean_target_gross=gross.mean(),max_abs_target_net=max_abs_net,
                baseline_gross_1_sections=int(np.isclose(gross,cfg["gross"]).sum()),valid_sections=len(gross),
                cash_sections=int((gross==0).sum()),mean_long=trace[:-1,2].clip(0).sum(1).mean(),
                mean_short=trace[:-1,2].clip(max=0).sum(1).mean(),asset_cap=cfg["single_asset_cap"]))
            acc["risk"].append(dict(**info,max_dd=perf["max_dd"],**holdings))
            b=bridge_rows[["ideal_label_pnl","close_label_vs_open_pnl","observed_proxy_missing_label_pnl",
                           "actual_quantity_drift_pnl","stale_price_mark_pnl","actual_price_pnl","funding_pnl","cost_pnl"]].sum()
            acc["bridge"].append(dict(**info,**bstats,**b.to_dict(),
                terminal_cost=float(l.iloc[-1][["fees","spread","slippage"]].sum()),net_pnl=l.equity.iloc[-1]-cfg["capital"],
                matched_price_pnl_error=float(b.iloc[:5].sum()-b.actual_price_pnl),status="PASS"))
            acc["daily"].append(turnover.assign(**info))
            cap=cfg["capital"]
            gross_path=perf["gross_return"]+perf["funding"]/cap
            fixed_summary,_,_=evaluate_portfolio(case,spec,static,cfg,packed,clean,funding[1],cost=main,fixed=True)
            for bps in cfg["cost_grid_bps"]:
                if bps==main:
                    alt=perf
                else:
                    alt,_,_=evaluate_portfolio(case,spec,static,cfg,packed,clean,funding[1],cost=bps)
                fixed_path=gross_path-fixed_path_cost(trace,bps)/cap
                if bps==main:
                    np.testing.assert_allclose(fixed_path,perf["net_return"],atol=1e-10)
                if bps==0:
                    zero=alt["net_return"]
                acc["cost"].extend([dict(**info,single_sided_cost_bps=bps,mode="same_executed_quantity_path",
                                         total_cost_return=fixed_path),
                                    dict(**info,single_sided_cost_bps=bps,mode="separate_dynamic_rerun",
                                         total_cost_return=alt["net_return"])])
            acc["portfolio"].append(dict(**info,**perf,zero_cost_rerun=zero,
                fixed_capital_simple_6bps=fixed_summary["net_return"],fixed_path_zero_return_explicit=gross_path,
                terminal_cost=float(l.iloc[-1][["fees","spread","slippage"]].sum()),
                terminal_turnover=float(l.turnover_usdt.iloc[-1]),execution="BAR_EXECUTION_PROXY"))
            acc["daily"][-1].to_parquet(output/"daily_turnover.parquet",index=False,compression="zstd")
            fields={k:acc[k][-1:] for k in ("weights","risk","bridge","portfolio")}
            fields["cost"]=acc["cost"][-2*len(cfg["cost_grid_bps"]):]
            write_json(receipt,dict(identity=identity,details=fields))
            print(f"REPLAY {split} {family} {method} {perf['net_return']:.5f}",flush=True)
        # Raw and Z-score L1 are equivalent under uniform positive scale; rank baseline matches Foundation.
        if cfg["nonlinearity"]["kind"]=="identity":
            for method in ("raw_l1","zscore_l1","rank_l1"):
                assert method in cfg["methods"]
            from portfolio.weights import weights as base_weights
            ref=np.stack([base_weights(x,"rank",cfg["gross"],cfg["single_asset_cap"],cfg["min_assets"])[1] for x in score])
            computed=np.stack([strength_to_weights(x,"rank_l1",cfg) for x in score])
            np.testing.assert_allclose(ref,computed,atol=1e-12)
            left=np.stack([strength_to_weights(x,"raw_l1",cfg) for x in score])
            right=np.stack([strength_to_weights(x,"zscore_l1",cfg) for x in score])
            np.testing.assert_allclose(left,right,atol=1e-12)
    write_json(out/"calibrators.json",families)
    # Regenerate only the light replay inputs needed to inspect frozen calibration,
    # from the saved signals and raw-label series rather than annual model fitting.
    for sex in acc["score"]:
        k=[sex[x] for x in ("split","model","model_version","factor_set")]
        market=dirs/k[0]/k[1]
        known=pd.read_parquet(market/"calibration_input.parquet")
        case=dict(scores=known.score.to_numpy().reshape(-1,len(cfg["symbols"])),
                  returns={60:known.raw_label.to_numpy().reshape(-1,len(cfg["symbols"]))})
        acc["calibration"].extend(dict(split=k[0],model=k[1],model_version=k[2],factor_set=k[3],
                                       calibration_fit="expanding TRAIN, sequential validation predictions",
                                       role="fit_diagnostic" if k[0]==VALIDATION else "outside_calibration",**r)
                                  for r in calibration_report(case,families[k[1]],prediction_to_strength))
    layout={"score":"prediction_statistics","decay":"signal_decay","strength":"relative_strength",
            "weights":"weight_comparison","bridge":"ic_return_bridge","risk":"portfolio_risk","daily":"turnover_analysis",
            "portfolio":"portfolio_comparison","cost":"cost_sensitivity","calibration":"score_calibration","covariance":"asset_covariance"}
    for k,name in layout.items():
        frame=(pd.concat(acc[k],ignore_index=True) if k=="daily" else pd.DataFrame(acc[k]))
        frame.to_csv(out/f"{name}.csv",index=False)
    _,final=verify(*paths.values())
    assert frozen==final
    test.update(ledgers=len(acc["bridge"]),risk_constraints="PASS",ic_return_reconciliation="PASS",source_custody=final)
    write_json(out/"tests.json",test)
    from portfolio_research.report import report
    report(out,cfg,test,perf_counter()-started,code_version())
    write_json(out/"resolved_config.json",cfg)
    write_artifact_manifest(out,"portfolio_research_v1")
    print("PORTFOLIO_RESEARCH_COMPLETE",flush=True)


if __name__=="__main__":
    cfg,paths,out=settings()
    cpu()
    with threadpool_limits(limits=2):
        run(cfg,paths,out)
