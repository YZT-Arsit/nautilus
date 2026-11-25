"""Thin Phase 4 adapter for the existing detailed and Nautilus ledgers."""
import numpy as np
import pandas as pd

from backtest.replay import replay as nautilus
from training.metrics import section_ic
from v2.execution import simulate,summarize

from portfolio_research.rules import audit_weights,rebalance,weights_to_target_positions


def evaluate_portfolio(case,spec,static,cfg,events,canonical_fund,proxy,cost=6,fixed=False,proof=False):
    px,t,s=case["prices"],case["execution"],case["scores"]
    fee,spread,slip=cost*2/3,cost/6,cost/6
    actual,details=simulate(t,px,cfg,events,scores=s,spec=spec,target=static,adv=np.ones_like(s),
                          fee=fee,spread=spread,slip=slip,fixed=fixed,detailed=proof,target_fn=rebalance)
    stats=summarize(actual,cfg["capital"])
    if stats["terminal_gross"]>1e-10 or stats["unsettled_funding"]:
        raise ValueError("unsettled funding or missing final close")
    if not proof:
        return stats,actual,None
    pack=np.asarray(details)
    wants=pack[:,2]
    audit_weights(wants,np.vstack([np.isfinite(s),np.zeros_like(s[:1],dtype=bool)]),cfg)
    a=nautilus(pd.DataFrame(px,index=t,columns=case["symbols"]),
               pd.DataFrame(wants,index=t,columns=case["symbols"]),canonical_fund,cfg["capital"],
               fee,spread,slip,proxy)
    for column in ("equity","gross_pnl","funding","fees","spread","slippage","turnover_usdt"):
        np.testing.assert_allclose(a[column],actual[column],atol=1e-7,rtol=2e-10,err_msg=column)
    assert a.skipped.to_numpy().tolist()==actual.reason.eq("MISSING_PRICE").tolist()
    return stats,actual,pack


def bridge(case,ledger,pack,capital):
    w=pack[:-1,2]
    labels=case["returns"][60]
    opening=case["prices"][:-1]
    closing=case["prices"][1:]
    base=ledger.equity.to_numpy()[:-1]+ledger[["fees","spread","slippage"]].sum(axis=1).to_numpy()[:-1]
    holding=pack[:-1,1]
    mark0=pack[:-1,5]
    mark1=pack[1:,5]
    delta=pack[:-1,4]
    observe=np.isfinite(labels)
    active=np.abs(w)>1e-14
    valid=np.isfinite(opening)&np.isfinite(closing)&(opening>0)
    proxy_return=np.divide(closing,opening,out=np.full_like(opening,np.nan),where=valid)-1
    ideal_obs=(w*np.where(observe,labels,0.)).sum(axis=1)
    ideal_complete=(~active|observe).all(axis=1)
    overlap=valid & observe
    base_sum=lambda x: x.sum(axis=1)
    observed_no_open=base_sum(w*np.where(observe&~valid,labels,0.))
    offset=base_sum(w*np.where(overlap,proxy_return-labels,0.))-observed_no_open
    no_label=base_sum(w*np.where(valid&~observe,proxy_return,0.))
    desired=base[:,None]*w
    actual_move=np.where(valid,closing-opening,0.)
    cross=base_sum(holding*actual_move)-base*base_sum(w*np.where(valid,proxy_return,0.))
    gap=base_sum(holding*(mark1-mark0))-base_sum(holding*actual_move)
    net_exec=ledger[["fees","spread","slippage"]].sum(axis=1).to_numpy()
    predicted=base*ideal_obs+base*offset+base*no_label+cross+gap
    pnl=ledger.gross_pnl.to_numpy()[1:]
    np.testing.assert_allclose(predicted,pnl,atol=1e-7)
    ret_net=predicted+ledger.funding.to_numpy()[1:]-net_exec[:-1]
    np.testing.assert_allclose(ret_net.sum()-net_exec[-1]+ledger.funding.iloc[0],
                               ledger.equity.iloc[-1]-capital,atol=1e-6)
    scores=case["scores"]
    pearson,n=section_ic(scores,labels,3)
    rank,_=section_ic(scores,labels,3,True)
    long=w.clip(0)
    short=-w.clip(max=0)
    y=np.where(observe,labels,0.)
    l=np.divide((long*y).sum(1),long.sum(1),out=np.full(len(w),np.nan),where=(long.sum(1)>0)&ideal_complete)
    sh=np.divide((short*y).sum(1),short.sum(1),out=np.full(len(w),np.nan),where=(short.sum(1)>0)&ideal_complete)
    f=pd.DataFrame(dict(prediction_time=case["time"],execution_time=case["execution"][:-1],label_end_time=case["execution"][1:],
                        pearson_ic=pearson,rank_ic=rank,paired_assets=n,
                        score_dispersion=np.nanstd(scores,axis=1),label_dispersion=np.nanstd(labels,axis=1),
                        long_basket_return=l,short_basket_return=sh,long_short_spread=l-sh,
                        ideal_dot=np.where(ideal_complete,ideal_obs,np.nan),ideal_partial=ideal_obs,
                        eligible_label_complete=ideal_complete,allocation_base=base,ideal_label_pnl=base*ideal_obs,
                        close_label_vs_open_pnl=base*offset,observed_proxy_missing_label_pnl=base*no_label,
                        actual_quantity_drift_pnl=cross,stale_price_mark_pnl=gap,actual_price_pnl=pnl,
                        funding_pnl=ledger.funding.to_numpy()[1:],cost_pnl=-net_exec[:-1],net_pnl=ret_net,
                        interval_gross_return=pnl/base,execution_skipped=ledger.reason.eq("MISSING_PRICE").to_numpy()[:-1]))
    # Verify quantity sizing and changes without inferring fills from future label eligibility.
    live=~f.execution_skipped.to_numpy()
    for i in np.flatnonzero(live):
        np.testing.assert_allclose(weights_to_target_positions(w[i],base[i],opening[i]),holding[i],atol=1e-7,rtol=1e-9)
    np.testing.assert_allclose(pack[:-1,0]+delta,holding,atol=1e-9)
    return f


def observation_stats(frame):
    valid=frame.dropna(subset=["pearson_ic","ideal_dot"])
    ideal=valid.ideal_dot
    actual=valid.interval_gross_return
    p=valid.pearson_ic
    pos=p>0
    low,high=np.nanquantile(p,[.25,.75])
    corr=np.corrcoef(p,ideal)[0,1]
    return dict(paired_sections=len(valid),ic_positive_sections=int(pos.sum()),mean_ic=p.mean(),
                average_theoretical_return=ideal.mean(),mean_actual_pre_cost_return=actual.mean(),
                positive_ic_positive_ideal_fraction=float((ideal[pos]>0).mean()),
                positive_ic_positive_actual_fraction=float((actual[pos]>0).mean()),
                positive_ic_mean_ideal=ideal[pos].mean(),ic_ideal_correlation=corr,
                low_ic_ideal_mean=ideal[p<=low].mean(),high_ic_ideal_mean=ideal[p>=high].mean(),
                low_ic_ideal_abs=ideal[p<=low].abs().mean(),high_ic_ideal_abs=ideal[p>=high].abs().mean())
