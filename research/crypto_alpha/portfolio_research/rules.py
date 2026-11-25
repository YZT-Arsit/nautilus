"""Replaceable strength, position and strictly lagged risk operations."""
import numpy as np
from scipy.stats import rankdata

from portfolio.weights import weights
from v2.execution import targets


def prediction_to_strength(scores, min_assets=3):
    s=np.asarray(scores,dtype=float)
    known=np.isfinite(s)
    result={k:np.full(s.shape,np.nan) for k in ("raw","demeaned","zscore","rank","rank_zscore","rank_l1")}
    result["raw"][known]=s[known]
    if known.sum()<min_assets:
        return result
    x=s[known]
    rank=rankdata(x,method="average")
    centered=x-x.mean()
    r=rank-rank.mean()
    result["rank"][known]=rank
    result["demeaned"][known]=centered
    result["zscore"][known]=centered/centered.std(ddof=0) if centered.std(ddof=0)>1e-12 else 0.
    if r.std(ddof=0)>1e-12:
        result["rank_zscore"][known]=r/r.std(ddof=0)
        result["rank_l1"][known]=r/np.abs(r).sum()
    else:
        result["rank_zscore"][known]=0.
        result["rank_l1"][known]=0.
    return result


def nonlinear(s,spec):
    if spec["kind"]=="identity":
        return s
    power=float(spec["power"])
    if spec["kind"]!="signed_power" or power<=0:
        raise ValueError("unknown research transform")
    return np.sign(s)*np.abs(s)**power


def apply_risk_constraints(s,cfg):
    return weights(np.asarray(s,dtype=float),"raw",cfg["gross"],cfg["single_asset_cap"],cfg["min_assets"])[1]


def estimate_risk(past,cfg):
    """Covariance, vol and BTC beta from same-row complete past data."""
    window=cfg["risk_window_hours"]
    n=cfg["risk_min_history"]
    p=past[-window:]
    k=p.shape[1]
    active=np.isfinite(p).sum(0)>=n
    active[0]=np.isfinite(p[:,0]).sum()>=n
    cov=np.full((k,k),np.nan)
    beta=np.full(k,np.nan)
    if active.sum()<2:
        return cov,beta
    x=p[:,active]
    good=np.isfinite(x).all(axis=1)
    if good.sum()<n:
        return cov,beta
    raw=np.cov(x[good],rowvar=False,ddof=1)
    cov[np.ix_(active,active)]=raw
    if active[0] and raw[0,0]>0:
        beta[active]=raw[:,0]/raw[0,0]
    return cov,beta


def risk_at_times(returns,cfg):
    out=[estimate_risk(returns[max(0,t-cfg["risk_window_hours"]):t],cfg) for t in range(len(returns))]
    return tuple(map(np.stack,zip(*out)))


def strength_to_weights(scores,method,cfg,cov=None):
    s=prediction_to_strength(scores,cfg["min_assets"])
    if method in {"rank_l1","raw_l1","zscore_l1"}:
        key={"rank_l1":"rank_zscore","raw_l1":"demeaned","zscore_l1":"zscore"}[method]
        x=nonlinear(s[key],cfg["nonlinearity"])
        return apply_risk_constraints(x,cfg)
    if method in {"top2","top3"}:
        v=np.flatnonzero(np.isfinite(scores))
        k=int(method[-1])
        if len(v)<2*k or np.ptp(scores[v])<=1e-12:
            return np.zeros_like(scores)
        order=v[np.argsort(scores[v],kind="stable")]
        code=np.zeros_like(scores)
        code[order[-k:]]=1
        code[order[:k]]=-1
        return apply_risk_constraints(code,cfg)
    if method!="risk_adjusted":
        raise ValueError(method)
    rank=s["rank_zscore"]
    diag=np.diag(cov)
    known=np.isfinite(rank)&np.isfinite(diag)&(diag>0)
    if known.sum()<cfg["min_assets"]:
        return np.zeros_like(scores)
    matrix=cov[np.ix_(known,known)]
    if not np.isfinite(matrix).all():
        return np.zeros_like(scores)
    a=cfg["covariance_diagonal_mix"]
    matrix=(1-a)*matrix+a*np.diag(np.diag(matrix))
    eigen,vec=np.linalg.eigh(matrix)
    floor=cfg["covariance_eigen_floor_fraction"]*np.median(np.diag(matrix))
    scaled=vec@((vec.T@rank[known])/np.sqrt(np.maximum(eigen,floor)))
    u=np.full_like(scores,np.nan)
    u[known]=scaled
    return apply_risk_constraints(nonlinear(u,cfg["nonlinearity"]),cfg)


def weights_to_target_positions(weights,equity,prices):
    if (np.asarray(weights)!=0)[~(np.isfinite(prices)&(prices>0))].any():
        raise ValueError("active quantity requires a valid execution mark")
    return np.divide(equity*weights,prices,out=np.zeros_like(weights),where=np.isfinite(prices)&(prices>0))


def rebalance(scores,drift,memory,spec,cfg,slope,adv,capital,cost_bps):
    # Existing V2 extensions are separate policy calls.
    return targets(scores,drift,memory,spec,cfg,slope,adv,capital,cost_bps)


def audit_weights(x,eligible,cfg):
    gross=np.abs(x).sum(axis=1)
    assert np.isfinite(x).all() and (np.abs(x).max()<=cfg["single_asset_cap"]+1e-12)
    np.testing.assert_allclose(x.sum(axis=1),0.,atol=1e-12)
    assert (gross<=cfg["gross"]+1e-12).all() and (x[~eligible]==0).all()
    return gross
