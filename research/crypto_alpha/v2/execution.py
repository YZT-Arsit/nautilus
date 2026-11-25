"""Independent bar-ledger reconciliation and causal portfolio policy."""
import numpy as np
import pandas as pd
from scipy.stats import rankdata

from portfolio.weights import weights


def neutral(w, cap, gross=1.):
    w=np.clip(w,-cap,cap)
    long=w.clip(0)
    short=(-w).clip(0)
    common=min(long.sum(),short.sum(),gross/2)
    if common<=0:
        return np.zeros_like(w)
    return long*(common/long.sum())-short*(common/short.sum())


def strength(scores):
    ranks=rankdata(scores,axis=-1,nan_policy="omit")
    center=ranks-np.nanmean(ranks,axis=-1,keepdims=True)
    denom=np.sqrt(np.nanmean(center**2,axis=-1,keepdims=True))
    return np.divide(center,denom,out=np.zeros_like(center),where=denom>1e-12)


def calibrate(scores, raw):
    """Univariate calibration in cross-sectional rank units from matured labels."""
    z=strength(scores)
    centered=raw-np.nanmean(raw,axis=1,keepdims=True)
    good=np.isfinite(z)&np.isfinite(centered)
    x,y=z[good],centered[good]
    return float(x@y/max(x@x,1e-12))


def targets(scores, drift, memory, spec, cfg, slope, adv, capital, cost_bps):
    rule=spec["rule"]
    if rule in ("rank","raw","equal"):
        return weights(scores,rule,cfg["gross"],cfg["max_weight"],cfg["min_assets"])[1]
    valid=np.isfinite(scores)
    if valid.sum()<cfg["min_assets"] or np.ptp(scores[valid])==0:
        return np.zeros_like(drift)
    rank=weights(scores,"rank",cfg["gross"],cfg["max_weight"],cfg["min_assets"])[1]
    cap=np.full(len(rank),cfg["max_weight"])
    if rule in ("top","buffer"):
        sorted_ids=np.flatnonzero(valid)[np.argsort(scores[valid],kind="stable")]
        k=spec["k"]
        def side(order, sign):
            kept=[j for j in order[:spec.get("exit_rank",k)] if rule=="buffer" and sign*drift[j]>1e-10]
            return list(dict.fromkeys(kept+list(order)))[:k]
        k=min(k,len(sorted_ids)//2)
        pos=side(sorted_ids[::-1],1)
        neg=side(sorted_ids,-1)
        w=np.zeros_like(rank)
        w[pos]=1
        w[neg]=-1
        return neutral(w,cap,cfg["gross"])
    mandatory=neutral(np.where(valid,drift,0.),np.where(valid,cap,0.),cfg["gross"])
    if rule=="ema":
        w=(1-spec["alpha"])*memory+spec["alpha"]*rank
        return neutral(np.where(valid,w,0),cap,cfg["gross"])
    if rule=="partial":
        return neutral(mandatory+spec["alpha"]*(rank-mandatory),cap,cfg["gross"])
    if rule=="opt":
        # Convex proximal method with explicit feasible constraints. Alternating
        # projection enforces box/L1/dollar limits on each iterate.
        cap=np.minimum(cap,np.nan_to_num(adv)*cfg["liquidity_fraction"]/max(capital,1e-9))
        cap=np.where(valid,cap,0)
        mandatory=neutral(drift,cap,cfg["gross"])
        alpha=slope*np.nan_to_num(strength(scores))
        risk=spec["risk"]
        fee=cost_bps/1e4
        w=mandatory.copy()
        for _ in range(12):
            candidate=w+.65/risk*(alpha-risk*w)
            trade=candidate-drift
            candidate=drift+np.sign(trade)*np.maximum(np.abs(trade)-.65/risk*fee,0.)
            w=neutral(candidate,cap,cfg["gross"])
        rank=w
    # Feasibility takes precedence when existing positions exceed caps or lose eligibility.
    limit=spec["limit"]
    mandatory_traded=np.abs(mandatory-drift).sum()
    distance=np.abs(rank-mandatory).sum()
    fraction=min(1.,max(0.,limit-mandatory_traded)/max(distance,1e-12))
    return mandatory+fraction*(rank-mandatory)


def simulate(times, prices, cfg, events, scores=None, spec=None, target=None, slope=0., adv=None,
             fee=4.,spread=1.,slip=1.,fixed=False, detailed=False,target_fn=targets):
    """Simulate fills from explicit target quantities. Funding precedes same-clock fills."""
    cash=cfg["capital"]
    qty=np.zeros(prices.shape[1])
    memory=np.zeros_like(qty)
    marks=np.zeros_like(qty)
    previous=cash
    last_entry=np.full(qty.shape,-1.)
    ledger=[]
    detail=[]
    j=0
    for i,(t,px) in enumerate(zip(times,prices)):
        t_ns=t.value
        payout=0.
        proxy_count=0
        unknown=0
        while j<len(events) and events[j][0]<=t_ns:
            timestamp,asset,rate,mark,is_proxy=events[j]
            if qty[asset]:
                if np.isfinite(mark):
                    payout-=qty[asset]*mark*rate
                    proxy_count+=is_proxy
                else:
                    unknown+=1
            j+=1
        old_marks=marks
        marks=np.where(np.isfinite(px),px,marks)
        mark_pnl=qty*(marks-old_marks)
        pnl=mark_pnl.sum()
        equity=previous+pnl+payout
        cash+=payout
        if equity<=0 and not fixed:
            raise ValueError(f"nonpositive equity at {t}")
        base=cfg["capital"] if fixed else equity
        drift=qty*marks/base
        terminal=i==len(times)-1
        active=True
        if target is not None:
            want=target[i]
        elif terminal:
            want=np.zeros_like(drift)
        else:
            cadence=spec.get("rebalance_minutes",0)
            active=not cadence or (t_ns-times[0].value)//60_000_000_000 % cadence==0
            # Ineligible positions are closed even between scheduled rebalances.
            risk_exit=bool(np.any((drift!=0)&~np.isfinite(scores[i])))
            active=active or risk_exit
            want=(target_fn(scores[i],drift,memory,spec,cfg,slope,adv[i],base,fee+spread+slip)
                  if active else drift)
        invalid=(~np.isfinite(px))&((qty!=0)|(want!=0))
        reason="NO_CHANGE"
        if not active:
            reason="BETWEEN_REBALANCES"
        elif invalid.any():
            reason="MISSING_PRICE"
        else:
            reason="TERMINAL" if terminal else "REBALANCE"
        delta=np.zeros_like(qty)
        commission=spread_cost=slip_cost=turn=0.
        if active and not invalid.any():
            desired=np.divide(base*want,px,out=qty.copy(),where=np.isfinite(px))
            delta=desired-qty
            delta=np.where(np.abs(delta)>1e-12,delta,0.)
            filled=marks*(1+np.sign(delta)*(spread+slip)/1e4)
            amounts=np.abs(delta)*filled
            turn=amounts.sum()
            commission=turn*fee/1e4
            adverse=np.abs(filled-marks)*np.abs(delta)
            if spread+slip:
                spread_cost=adverse.sum()*spread/(spread+slip)
                slip_cost=adverse.sum()*slip/(spread+slip)
            cash-=delta@filled+commission
            if not turn and not terminal:
                reason="COST_GATE" if spec and spec["rule"]=="opt" else "NO_CHANGE"
            memory=want if turn or target is None else memory
        new_qty=qty+delta
        ended=(qty!=0)&((new_qty==0)|(np.sign(qty)!=np.sign(new_qty)))
        started=(new_qty!=0)&((qty==0)|(np.sign(qty)!=np.sign(new_qty)))
        duration=((t_ns-last_entry[ended])/3.6e12).tolist()
        last_entry[ended]=-1
        last_entry[started]=t_ns
        pnl_net=pnl+payout-commission-spread_cost-slip_cost
        new_equity=cash+new_qty@marks
        if detailed:
            detail.append((qty.copy(),new_qty.copy(),want.copy(),drift.copy(),delta.copy(),marks.copy()))
        ledger.append((t,new_equity,pnl,payout,commission,spread_cost,slip_cost,turn,turn/(cfg["capital"] if fixed else previous),
                       pnl_net,reason,len(duration),sum(duration),proxy_count,unknown,float(np.abs(new_qty*marks/base).sum()),
                       float((new_qty*marks/base).sum()),np.abs(delta*marks/base).sum(),
                       mark_pnl[qty>0].sum(),mark_pnl[qty<0].sum()))
        previous=new_equity
        qty=new_qty
    columns="execution_time equity gross_pnl funding fees spread slippage turnover_usdt turnover_equity net_pnl reason closed_positions duration_hours funding_proxy_count funding_unsettled gross_exposure net_exposure traded_pre_equity gross_long_pnl gross_short_pnl".split()
    return pd.DataFrame(ledger,columns=columns),detail


def summarize(ledger, capital):
    equities=np.r_[capital,ledger.equity]
    daily_equity=np.r_[capital,ledger.groupby(ledger.execution_time.dt.floor("D")).equity.last()]
    day=np.diff(daily_equity)/daily_equity[:-1]
    closed=ledger.closed_positions.sum()
    day_n=max(1,ledger.execution_time.dt.floor("D").nunique())
    turn=ledger.turnover_usdt.sum()
    pnl=ledger.gross_pnl.sum()
    funding=ledger.funding.sum()
    return dict(net_return=equities[-1]/capital-1,gross_return=pnl/capital,
                fixed_path_zero_return=(pnl+funding)/capital,
                max_dd=float((1-equities/np.maximum.accumulate(equities)).max()),
                sharpe=float(day.mean()/day.std(ddof=1)*np.sqrt(365)) if len(day)>1 and day.std(ddof=1)>0 else np.nan,
                daily_turnover=ledger.turnover_equity.sum()/day_n,turnover_usdt=turn,
                be_bps=(pnl+funding)*1e4/turn if turn else np.nan,
                funding=funding,fees=ledger.fees.sum(),spread=ledger.spread.sum(),slippage=ledger.slippage.sum(),
                mean_holding_hours=ledger.duration_hours.sum()/closed if closed else np.nan,
                trades=int((ledger.turnover_usdt>0).sum()),funding_proxy_count=int(ledger.funding_proxy_count.sum()),
                unsettled_funding=int(ledger.funding_unsettled.sum()),terminal_gross=float(ledger.gross_exposure.iloc[-1]),
                max_abs_net=float(ledger.net_exposure.abs().max()),max_gross=float(ledger.gross_exposure.max()))
