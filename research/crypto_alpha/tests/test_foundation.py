from types import SimpleNamespace

import numpy as np
import pandas as pd
import torch

from foundation.dataset import CrossSectionDataset, task
from foundation.evaluation import quantile_bins, quantiles, relative_strength, target_weights
from foundation.registry import Factor, Registry
from labels.alignment import align
from models.cpu import ic_loss
from training.protocol import annual_windows


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def test_delayed_label_uses_exact_completed_prices_at_every_supported_frequency():
    times = pd.date_range("2025-12-31 23:00", periods=601, freq="min",tz="UTC")
    prices = pd.Series(100+np.arange(len(times)), index=times,dtype=float)
    missing = prices.drop(utc("2026-01-01 00:10"))
    for h in (30,60,240,480):
        aligned = align(times[:1], prices, 10, h).iloc[0]
        assert aligned.entry_time == utc("2025-12-31 23:10")
        assert aligned.label_available_time == aligned.entry_time+pd.Timedelta(minutes=h)
        assert aligned.label_raw == (110+h)/110-1
    assert np.isnan(align(times[60:61],missing,10,60).iloc[0].label_raw)


def test_slice_does_not_expose_any_normalized_label_before_eligible_cohort_matures():
    t = utc("2025-06-30 23:00")
    base = pd.DataFrame({"prediction_time":t,"symbol":list("ABCD"),"eligible":[True,True,True,False],
                         "feature_available_time":t,"return_60":[.1,.2,np.nan,.5],"open_end_60":100.,
                         "close_end_60":100.,"f":[1.,2.,3.,np.nan]})
    shaped = task(base,60,60,10,2,1e-12)
    shaped.loc[2,"label_available_time"] = utc("2025-07-01 01:10")
    p = CrossSectionDataset(shaped,list("ABCD"),["f"],2)
    early = p.view(t,utc("2025-07-01 00:10"))
    assert np.isnan(early["labels"]["label_rank_zscore"]).all()
    np.testing.assert_array_equal(early["eligible"],[[True,True,True,False]])
    late = p.view(t,utc("2025-07-01 01:10"))
    np.testing.assert_allclose(late["labels"]["label_rank_zscore"],[[-1,1,np.nan,np.nan]],equal_nan=True)


def test_registered_factor_and_core_are_causal_on_minute_inputs():
    x = np.arange(3000,dtype=float)
    index = pd.date_range("2025-01-01",periods=len(x),freq="min",tz="UTC")
    bars = pd.DataFrame(dict(close=100+x/100,open=100+x/100,high=100+(x+1)/100,
                             low=100+(x-1)/100,volume=2+x/1000,quote_volume=200+x/10,
                             trade_count=10+x%7,taker_buy_volume=1+x/4000,taker_buy_quote_volume=110+x/30),index=index)
    future = bars.copy()
    future.loc[index[2401]:] *= 3
    grid = index[::30]
    events = [SimpleNamespace(event_time_ns=index[100].value, funding_rate=.0001)]
    r = Registry()
    r.register(Factor("own", "mean close", "close", "3m", lambda b, window: b.close.rolling(window).mean(),
                      parameters={"window":3}))
    a, rows = r.symbol(bars,grid,events)
    b,_ = r.symbol(future,grid,events+[SimpleNamespace(event_time_ns=index[2420].value,funding_rate=.1)])
    pd.testing.assert_frame_equal(a.loc[:index[2400]], b.loc[:index[2400]])
    assert a.loc[index[2400],"own"] == bars.close.iloc[2398:2401].mean()
    assert len(rows)==77  # 76 built-in per-symbol factors plus the registered factor.

    def section_factor(known):
        assert "future_label" not in known
        assert known.loc[~known.eligible,"ret_30m"].isna().all()
        return known.groupby("prediction_time").ret_30m.transform("mean")
    r.register(Factor("section_mean", "mean of current returns", "ret_30m", "30m", section_factor,
                      stage="section"))
    paired = pd.concat([a.assign(symbol=symbol, eligible=i==0, prediction_time=a.index,
                                 feature_available_time=a.index, future_label=999)
                        for i,symbol in enumerate(["BTCUSDT","ETHUSDT"])],ignore_index=True)
    assert "section_mean" in r.section(paired,rows).factor_name.to_list()



def test_strength_ties_zero_variance_and_neutral_one_x_targets():
    index = pd.MultiIndex.from_product([[utc("2025-01-01")],list("ABCDE")],names=["prediction_time","symbol"])
    x = pd.Series([3.,1.,1.,np.nan,4.],index=index)
    rank = relative_strength(x,"rank")
    np.testing.assert_allclose(rank,[3.,1.5,1.5,np.nan,4.])
    z = relative_strength(x,"rank_zscore")
    assert abs(z.mean()) < 1e-12 and abs(z.std(ddof=0)-1) < 1e-12
    w = relative_strength(z,"l1").fillna(0)
    _, weights = target_weights(np.array([x.to_numpy()]),{"gross":1.,"max_weight":1.,"min_assets":3},"rank")
    np.testing.assert_allclose(w,weights[0])
    assert relative_strength(pd.Series(1.,index=index),"zscore").isna().all()


def test_annual_calendar_and_three_year_requirement():
    a = list(annual_windows(utc("2024-07-01"),utc("2026-07-01"),1,3))
    assert len(a)==1 and a[0].test_start==utc("2025-07-01") and a[0].validation_start==utc("2025-04-01")
    assert not list(annual_windows(utc("2024-07-01"),utc("2026-07-01"),2,3))
    b = list(annual_windows(utc("2024-07-01"),utc("2028-07-01"),2,3))
    assert [w.test_start.year for w in b] == [2026,2027]


def test_ic_gradient_against_numerical_differentiation_and_constant_targets():
    pred = torch.tensor([[3.,1.,4.,2.],[1.,0.,4.,2.],[0.,0.,0.,0.]], dtype=torch.float64,requires_grad=True)
    y = torch.tensor([[.1,.3,.2,0.],[.5,.2,.1,.4],[1.,1.,1.,1.]],dtype=torch.float64)
    mask = torch.tensor([[True,True,True,False],[True,True,True,True],[True,True,True,True]])
    assert torch.autograd.gradcheck(lambda x: ic_loss(x,y,mask), (pred,))
    r = np.mean([np.corrcoef(a[m],b[m])[0,1] for a,b,m in zip(pred.detach().numpy()[:2],y.numpy()[:2],mask.numpy()[:2])])
    np.testing.assert_allclose(ic_loss(pred,y,mask).item(),1-r)
    assert ic_loss(pred,y,mask).backward() is None
    assert torch.isfinite(pred.grad).all() and pred.grad[-1].eq(0).all()


def test_quantile_turnover_is_outcome_independent():
    x = np.array([[1.,2.,3.,4.,5.,6.],[6.,1.,2.,3.,4.,5.]])
    y = x/100
    assert quantile_bins(np.ones((1,6))).sum()==0
    _, t = quantiles(x,y)
    changed = y.copy()
    changed[1,0] = np.nan
    _, t_missing = quantiles(x,changed)
    np.testing.assert_allclose(t[3],t_missing[3],equal_nan=True)
    assert t[3][1]==.5
