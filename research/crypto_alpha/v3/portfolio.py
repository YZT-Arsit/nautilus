"""Train-OOF-calibrated return weights and cost-based order gate."""
import numpy as np
import pandas as pd

from portfolio.weights import weights
from v2.execution import neutral,simulate,strength,summarize,targets
from v2.train import execution_view


def policy(scores,drift,memory,spec,cfg,slope,adv,capital,cost):
    rule=spec["rule"]
    if rule not in ("v3_gate","v3_return"):
        return targets(scores,drift,memory,spec,cfg,slope,adv,capital,cost)
    valid=np.isfinite(scores)
    cap=np.where(valid,np.minimum(cfg["max_weight"],np.nan_to_num(adv)*cfg["liquidity_fraction"]/max(capital,1)),0.)
    if valid.sum()<cfg["min_assets"]:
        return np.zeros_like(drift)
    mu=slope*strength(scores)
    signal=np.where(valid,mu,np.nan)
    want=neutral(weights(signal,"raw",cfg["gross"],cfg["max_weight"],cfg["min_assets"])[1],cap,cfg["gross"])
    if rule=="v3_return":
        return want
    mandatory=neutral(drift,cap,cfg["gross"])
    delta=want-drift
    hurdle=(cost+cfg["risk_buffer_bps"])*1e-4*np.abs(delta).sum()
    gain=np.nansum(mu*(want-mandatory))
    return want if gain>hurdle else mandatory


def variants(cfg):
    return [*cfg["frozen_portfolios"],
            {"name":"calibrated_return","rule":"v3_return"},
            {"name":"no_trade_region","rule":"v3_gate"}]


def replay(source,panel,view,scores,spec,slope,events,cfg,cost=6,fixed=False,full=False):
    t,px=execution_view(source,view,panel.cadence,cfg["delay_minutes"],panel.symbols)
    # execution_view sets the terminal marker using the actual forecast cadence.
    adv=np.expm1(panel.matrix("log_quote_volume")[view["selector"]])
    ledger,d=simulate(t,px,cfg,events,scores=scores,spec=spec,slope=slope,adv=adv,
                      fee=cost*2/3,spread=cost/6,slip=cost/6,fixed=fixed,detailed=full,target_fn=policy)
    stats=summarize(ledger,cfg["capital"])
    if stats["unsettled_funding"] or stats["terminal_gross"]>1e-10:
        raise ValueError("backtest has unsettled funding or an unclosed terminal position")
    trace=None
    if full:
        a=np.asarray(d)
        holdings=a[:,1,:]*a[:,5,:]/ledger.equity.to_numpy()[:,None]
        beta=panel.matrix("btc_beta")[view["selector"]]
        beta=np.vstack([beta,beta[-1:]])
        beta_unknown=(~np.isfinite(beta)&(holdings!=0)).any(axis=1)
        exposure=np.where(beta_unknown,np.nan,(np.nan_to_num(beta)*holdings).sum(axis=1))
        stats.update(long_exposure=holdings.clip(0).sum(axis=1).mean(),
                     short_exposure=holdings.clip(max=0).sum(axis=1).mean(),
                     btc_beta=np.nanmean(exposure))
        trace=pd.DataFrame(dict(execution_time=np.repeat(t,len(panel.symbols)),symbol=panel.symbols*len(t),
                                target_weight=a[:,2,:].ravel(),actual_weight=holdings.ravel(),executed_qty=a[:,4,:].ravel()))
    return stats,ledger,trace


def full_cost(source,panel,view,scores,spec,slope,events,cfg):
    p,ledger,trace=replay(source,panel,view,scores,spec,slope,events,cfg,full=True)
    simple,_,_=replay(source,panel,view,scores,spec,slope,events,cfg,fixed=True)
    grid=[]
    for bps in (0,1,2,4,6,8,10):
        t=p if bps==6 else replay(source,panel,view,scores,spec,slope,events,cfg,cost=bps)[0]
        grid.extend([dict(bps=bps,mode="fixed_execution_path",return_net=p["fixed_path_zero_return"]-p["turnover_usdt"]*bps/1e4/cfg["capital"]),
                     dict(bps=bps,mode="dynamic_rerun",return_net=t["net_return"])])
        if bps==0:
            p["zero_cost_rerun"]=t["net_return"]
    p["fixed_capital_simple_6bps"]=simple["net_return"]
    return p,ledger,trace,grid
