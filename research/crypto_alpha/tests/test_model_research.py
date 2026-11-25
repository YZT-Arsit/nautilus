import numpy as np
import pytest
import torch

from model_research.model import Trial,network,original_stockmixer_loss,prepared_label
from models.cpu import ic_loss


def test_ic_loss_independent_sections_constant_masks_and_ties():
    pred=torch.tensor([[1.,2.,3.,5.],[8.,8.,8.,8.],[4.,3.,1.,0.],[1.,2.,3.,4.]],
                      dtype=torch.float64,requires_grad=True)
    y=torch.tensor([[1.,1.,4.,5.],[3.,2.,1.,7.],[1.,1.,1.,1.],[1.,2.,3.,4.]],dtype=torch.float64)
    mask=torch.tensor([[True,True,True,False],[True,True,True,True],[True,True,True,True],[False,True,False,True]])
    result=ic_loss(pred,y,mask)
    corr=np.corrcoef([1,2,3],[1,1,4])[0,1]
    np.testing.assert_allclose(result.item(),1-corr/2)
    result.backward()
    assert torch.isfinite(pred.grad).all()
    assert pred.grad[0,-1]==0 and pred.grad[2:].eq(0).all()
    smooth=lambda x:ic_loss(x,y[:1],mask[:1])
    assert torch.autograd.gradcheck(smooth,(pred[:1].detach().requires_grad_(),))


def test_stockmixer_pair_loss_stays_inside_time_sections():
    p=torch.tensor([[1.,0.,2.],[93.,110.,108.]])
    y=torch.tensor([[2.,1.,0.],[100.,90.,122.]])
    m=torch.tensor([[True,True,False],[True,True,True]])
    a=original_stockmixer_loss(p,y,m)
    b=torch.stack([original_stockmixer_loss(p[i:i+1],y[i:i+1],m[i:i+1]) for i in range(2)]).mean()
    torch.testing.assert_close(a,b)


def test_every_ablation_has_finite_forward_with_missing_assets():
    x=torch.randn(2,9,8,9)
    m=torch.tensor([[True,False,True,True,False,False,True,True,True],[False]*9])
    for family,variants in {"MASTER":["full","no_gate","no_time","no_asset","no_time_no_asset"],
                           "StockMixer":["full","no_factor_mix","no_time_mix","no_asset_mix"],
                           "GRU":["full"]}.items():
        for variant in variants:
            model=network(Trial(family,"IC",variant=variant),9,9,8)
            pred=model(x,m)
            assert pred.shape==m.shape and torch.isfinite(pred).all()


def test_raw_rank_labels_use_same_maturity_and_asset_membership():
    raw=np.array([[.01,.02,.03],[.01,.01,.01]])
    rank=np.array([[-1.,np.nan,1.],[np.nan]*3])
    view={"raw":raw,"labels":{"label_rank_zscore":rank}}
    np.testing.assert_array_equal(np.isfinite(prepared_label(view,"raw")),np.isfinite(prepared_label(view,"rank")))


def test_annual_refit_deserializes_best_for_each_learner():
    from model_research.study import frozen_rounds
    assert frozen_rounds(np.nan) is None
    assert frozen_rounds(None) is None
    assert type(frozen_rounds(3.0)) is int
    with pytest.raises(ValueError):
        frozen_rounds(2.5)
