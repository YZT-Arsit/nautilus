import numpy as np
import pandas as pd
import torch
from models.cpu import ic_loss, fit_nn, torch_prediction
from training.protocol import annual_windows

def test_ic_loss_is_cross_sectional_and_gradient_finite():
    pred=torch.tensor([[1.,1.,1.,10.],[1.,2.,3.,4.]],requires_grad=True)
    labels=torch.tensor([[1.,2.,3.,0.],[1.,1.,1.,1.]])
    valid=torch.tensor([[True,True,True,False],[True,True,True,True]])
    loss=ic_loss(pred,labels,valid)
    loss.backward()
    assert torch.isfinite(pred.grad).all()
    assert pred.grad[0,3] == 0 and pred.grad[1].abs().sum() == 0

def test_cpu_mlp_reproducible_and_annual_schedule():
    x=np.random.default_rng(23).normal(size=(10,4,3)).astype(np.float32)
    cfg={"seed":42,"min_assets":3}
    a,_,_=fit_nn(x,x[:,:,0],None,cfg,"MLP-IC",epochs=2)
    b,_,_=fit_nn(x,x[:,:,0],None,cfg,"MLP-IC",epochs=2)
    np.testing.assert_array_equal(torch_prediction(a,x),torch_prediction(b,x))
    start=pd.Timestamp("2023-07-01",tz="UTC")
    end=pd.Timestamp("2026-07-01",tz="UTC")
    assert len(list(annual_windows(start,end,1,3))) == 2
    assert len(list(annual_windows(start,end,2,3))) == 1
