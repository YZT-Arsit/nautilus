import numpy as np
import pandas as pd
import torch

from v2.execution import neutral, simulate, targets
from v2.networks import history,construct


def test_sequence_uses_current_and_prior_time_only():
    x=np.arange(60).reshape(10,2,3)
    h=history(x,4)
    np.testing.assert_array_equal(h[5,0],x[2:6,0])
    changed=x.copy()
    changed[6:]*=1000
    np.testing.assert_array_equal(h[:6],history(changed,4)[:6])


def test_models_are_cross_section_batched_and_finite():
    x=torch.randn(3,9,8,7)
    eligible=torch.ones(3,9,dtype=torch.bool)
    eligible[1]=False
    for family in ("GRU","StockMixer","MASTER"):
        model=construct(family,7,9,8).eval()
        a=model(x,eligible)
        assert a.shape==(3,9)
        assert torch.isfinite(a).all()
        with torch.inference_mode():
            inputs=x.clone()
            inputs[2]*=20
            torch.testing.assert_close(a[:2],model(inputs,eligible)[:2])


def test_turnover_targets_use_drift_exclude_ineligible_and_preserve_balance():
    cfg={"max_weight":.30,"gross":1.,"min_assets":3}
    drift=np.array([.25,0.,-.25,0.])
    new=targets(np.array([1.,2.,4.,3.]),drift,drift,{"rule":"turnover","limit":.2},cfg,0.,np.ones(4),1.,6.)
    assert abs(new.sum())<1e-12
    assert np.abs(new-drift).sum()<=.2+1e-12
    scores=np.array([np.nan,1.,4.,3.])
    limited=targets(scores,drift,drift,{"rule":"turnover","limit":.1},cfg,0.,np.ones(4),1.,6.)
    assert limited[0]==0.
    assert abs(limited.sum())<1e-12


def test_cash_path_attribution_and_cost_removal():
    t=pd.date_range("2025-01-01",periods=3,freq="h",tz="UTC")
    px=np.array([[1,1],[1.1,.9],[1.02,.95]])
    w=np.array([[.5,-.5],[.5,-.5],[0,0.]])
    ledger,_=simulate(t,px,{"capital":10000.},[],target=w)
    np.testing.assert_allclose(ledger.net_pnl.sum(),ledger.equity.iloc[-1]-10000.)
    assert 0<ledger.turnover_usdt.iloc[1]<1500


def test_added_features_are_causal_and_funding_is_strictly_known():
    from types import SimpleNamespace
    from v2.features import local_features
    index=pd.date_range("2025-01-01",periods=300,freq="min",tz="UTC")
    price=np.linspace(100.,120.,len(index))
    bars=pd.DataFrame(dict(close=price,open=price-.01,high=price+.01,low=price-.05,
                           quote_volume=np.arange(len(index))+10.,trade_count=np.arange(len(index))+1,
                           taker_buy_quote_volume=np.arange(len(index))*.4),index=index)
    cutoff=index[260]
    fund=[SimpleNamespace(event_time_ns=index[j].value,funding_rate=x) for j,x in
          [(250,.001),(255,.002),(260,.999),(270,1.0)]]
    one=pd.DatetimeIndex([cutoff])
    a,registry=local_features(bars,one,fund)
    b,_=local_features(bars.loc[:cutoff],one,fund[:2])
    pd.testing.assert_frame_equal(a,b)
    assert np.isclose(a["v2_funding_average"].iloc[0],.0015)
    assert len(registry)==23


def test_horizon_uses_traded_label_horizon_but_has_separate_execution_terminal():
    from v2.features import horizon_panel
    t=pd.Timestamp("2025-01-01",tz="UTC")
    source=pd.DataFrame(dict(prediction_time=[t]*3,symbol=["A","B","C"],eligible=[True]*3,
                             ret_label_240=[.01,.02,-.01],terminal_60=[2.,3.,4.]))
    frame=horizon_panel(source,60,240,10,3,1e-12)
    assert frame.entry_time.iloc[0]==t+pd.Timedelta(minutes=10)
    assert frame.label_available_time.iloc[0]==t+pd.Timedelta(minutes=250)
    assert frame.terminal_execution_price_proxy.iloc[0]==2.
    assert frame.label_rank_zscore.notna().all()


def test_report_matching_treats_missing_variant_as_unspecified():
    from v2.report import matching
    key=pd.Series(dict(model="M",loss="IC",prediction_minutes=60,label_minutes=60,variant=np.nan))
    observed=pd.DataFrame([dict(model="M",loss="IC",prediction_minutes=60,label_minutes=60,variant="rank")])
    assert matching(observed,key).variant=="rank"
