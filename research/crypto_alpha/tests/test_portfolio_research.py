from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from portfolio_research.rules import (prediction_to_strength,strength_to_weights,weights_to_target_positions,
                                      risk_at_times,audit_weights)
from portfolio_research.engine import evaluate_portfolio,bridge
from portfolio_research.diagnostics import fit_calibration
from portfolio_research.main import funding_book,fixed_path_cost
from v3.common import write_json


def config():
    return dict(gross=1.,single_asset_cap=.5,min_assets=3,nonlinearity={"kind":"identity"},
                risk_min_history=3,risk_window_hours=5,capital=10000.)


def test_strength_ties_order_and_l1_scale_equivalence():
    cfg=config()
    x=np.array([0.,2.,2.,-1.,np.nan,4.])
    d=prediction_to_strength(x)
    np.testing.assert_array_equal(d["rank"],[2,3.5,3.5,1,np.nan,5])
    assert np.nanstd(d["rank_zscore"],ddof=0)==pytest.approx(1.)
    a=strength_to_weights(x,"raw_l1",cfg)
    b=strength_to_weights(x,"zscore_l1",cfg)
    np.testing.assert_allclose(a,b)
    np.testing.assert_allclose(strength_to_weights(x,"rank_l1",cfg),np.nan_to_num(d["rank_l1"]))
    assert not strength_to_weights(np.ones_like(x),"rank_l1",cfg).any()


def test_weight_single_cap_and_quantity_conversion():
    cfg={**config(),"single_asset_cap":.30}
    x=np.arange(7,dtype=float)
    for m in ("rank_l1","top2","top3"):
        w=strength_to_weights(x,m,cfg)
        audit_weights(w[None],np.ones_like(w[None],dtype=bool),cfg)
        assert np.abs(w).sum()==pytest.approx(1.)
        px=np.arange(1,8,dtype=float)
        np.testing.assert_allclose(weights_to_target_positions(w,1000.,px)*px,w*1000.)
    with pytest.raises(ValueError):
        weights_to_target_positions(np.array([1.]),1000.,np.array([np.nan]))


def test_risk_depends_strictly_on_past_rows():
    x=np.random.default_rng(3).normal(size=(15,3))
    y=x.copy()
    y[8:]*=100
    a,p=risk_at_times(x,config())
    b,q=risk_at_times(y,config())
    np.testing.assert_array_equal(a[:9],b[:9])
    np.testing.assert_array_equal(p[:9],q[:9])
    assert not np.allclose(a[-1],b[-1])


def test_actual_drift_delta_missing_prices_and_terminal_accounting():
    cfg=config()
    t=pd.date_range("2025-01-01 00:10",freq="h",periods=4,tz="UTC")
    prices=np.array([[100.,100.,100.],[105.,100.,100.],[107.,np.nan,100.],[109.,106.,103.]])
    x=np.tile([1.,2.,4.],(3,1))
    w=np.tile([-.5,0.,.5],(4,1))
    w[2]=[-.3,-.2,.5] # missing B quote must prevent the update
    w[-1]=0
    case=dict(prices=prices,execution=t,scores=x,symbols=list("ABC"),
              time=t[:-1]-pd.Timedelta(minutes=10),returns={60:np.array([[.05,0,0],[.02,np.nan,0],[.02,.04,.03]])})
    stats,l,tr=evaluate_portfolio(case,None,w,cfg,[],{}, {}, proof=True)
    f=bridge(case,l,tr,cfg["capital"])
    assert l.reason.iloc[2]=="MISSING_PRICE"
    np.testing.assert_array_equal(tr[2,0],tr[2,1])
    assert not np.allclose(w[1],tr[1,3])
    assert np.abs(tr[1,4]).sum() < np.abs(tr[1,0]).sum()
    assert stats["terminal_gross"]==0 and l.fees.iloc[-1]>0 and f.execution_skipped.iloc[-1]
    np.testing.assert_allclose(fixed_path_cost(tr,6),l[["fees","spread","slippage"]].sum().sum())


def test_same_timestamp_funding_deduplication():
    t=pd.date_range("2025-01-01",freq="h",periods=3,tz="UTC")
    case=dict(execution=t,symbols=["BTC"])
    x=SimpleNamespace(event_time_ns=t[1].value,mark_price=111.,funding_rate=.001)
    events,clean=funding_book(case,{"BTC":[x,x]}, {})
    assert len(events)==len(clean["BTC"])==1
    y=SimpleNamespace(event_time_ns=x.event_time_ns,mark_price=111.,funding_rate=.002)
    with pytest.raises(ValueError):
        funding_book(case,{"BTC":[x,y]}, {})


def test_calibration_coefficients_and_quantile_edges_are_persistable(tmp_path):
    scores=np.tile([1.,2.,3.,4.,5.,6.,7.,8.,9.],(4,1))
    fitted=fit_calibration({"scores":scores,"returns":{60:scores/100}},prediction_to_strength,5)
    path=tmp_path/"fitted.json"
    write_json(path,fitted)
    assert len(fitted["rank_quantiles"]["edges"])>1 and path.stat().st_size>0
