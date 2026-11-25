import numpy as np
import pandas as pd
import torch

from v2.execution import simulate
from v3.features import longest_run
from v3.objectives import loss_function
from v3.portfolio import policy


def test_optional_execution_policy_preserves_frozen_default():
    times=pd.date_range("2025-01-01",periods=3,freq="h",tz="UTC")
    px=np.ones((3,3))
    cfg={"capital":1000.,"min_assets":3,"max_weight":.3,"gross":1.}
    scores=np.array([[.1,.2,.3],[.3,.2,.1]])
    adv=np.ones_like(scores)
    l,_=simulate(times,px,cfg,[],scores=scores,spec={"rule":"rank"},adv=adv)
    alt,_=simulate(times,px,cfg,[],scores=scores,spec={"rule":"rank"},adv=adv,target_fn=policy)
    pd.testing.assert_frame_equal(l,alt)
    assert l.turnover_usdt.sum()>0


def test_cost_gate_mandatory_risk_removal_even_when_estimated_alpha_zero():
    cfg={"max_weight":.3,"min_assets":3,"gross":1.,"liquidity_fraction":.001,"risk_buffer_bps":1}
    d=np.array([.12,-.15,-.05,.08])
    w=policy(np.array([np.nan,1.,2.,3.]),d,d,{"rule":"v3_gate"},cfg,0.,np.full(4,1e6),1000.,6.)
    assert w[0]==0
    assert abs(w.sum())<1e-12


def test_temporal_penalty_respects_gaps_and_ic_is_cross_sectional():
    pred=torch.tensor([[1.,2.,3.],[3.,2.,1.]])
    truth=pred.clone()
    known=torch.ones_like(pred,dtype=torch.bool)
    times=torch.tensor([0,480*60_000_000_000])
    assert loss_function("IC_TEMP",pred,truth,known,truth,known,times,240)<1e-6
    adjacent=torch.tensor([0,240*60_000_000_000])
    assert loss_function("IC_TEMP",pred,truth,known,truth,known,adjacent,240)>0


def test_longest_run_is_not_a_union_of_sparse_dates():
    dates=pd.date_range("2026-07-01",periods=7)
    result=longest_run(pd.Series([True,False,True,True,True,False,True],index=dates))
    assert result==list(dates[2:5])
