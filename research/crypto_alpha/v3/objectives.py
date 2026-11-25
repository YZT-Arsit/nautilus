"""Causal annual samples and complete-section objectives."""
from dataclasses import dataclass
from time import perf_counter

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.nn import functional as F

from models.cpu import Preprocessor,fit_model,prediction,net,ic_loss
from training.metrics import section_ic,score_summary
from v2.networks import construct,history,predict


@dataclass(frozen=True)
class Trial:
    name: str
    family: str
    horizon: int=240
    objective: str="MSE"
    label: str="rank"
    subset: str="selected"
    seed: int=42


def target(view, names, label):
    if label=="rank":
        return view["labels"]["label_rank_zscore"].copy()
    if label=="zscore":
        return view["labels"]["label_raw_zscore"].copy()
    if label=="residual":
        beta=view["x"][:,:,names.index("btc_beta")]
        return view["raw"]-beta*view["raw"][:,:1]
    if label=="raw":
        return view["raw"].copy()
    raise ValueError(label)


def diagnostics(scores,view,processed):
    ic,_=section_ic(scores,view["raw"],3)
    rank,_=section_ic(scores,view["raw"],3,True)
    yic,_=section_ic(scores,processed,3)
    stats=score_summary(ic)
    return dict(ic=stats["mean"],rank_ic=score_summary(rank)["mean"],icir=stats["ir"],
                processed_ic=score_summary(yic)["mean"],positive_ic_ratio=stats["positive_ratio"],
                valid_sections=stats["valid_sections"]),ic,rank


class SectionMLP(nn.Module):
    def __init__(self,features):
        super().__init__()
        self.model=net(features)
    def forward(self,x,eligible):
        return self.model(x).squeeze(-1)


def transform(prep,panel,features,family):
    x=prep.transform(panel.x[:,:,features])
    x=np.where(panel.eligible[:,:,None],x,0.).astype(np.float32,copy=False)
    return history(x,8) if family in ("GRU","StockMixer","MASTER") else x


def construct_v3(family,n,assets):
    return SectionMLP(n) if family=="MLP" else construct(family,n,assets,8)


def loss_function(kind,score,y,valid,raw,raw_valid,times,interval):
    if kind=="MSE":
        return F.mse_loss(score[valid],y[valid])
    base=ic_loss(score,y,valid)
    if kind=="IC_HUBER" and raw_valid.any():
        base=base+.20*F.huber_loss(score[raw_valid],raw[raw_valid],delta=1.)
    elif kind=="IC_TEMP":
        continuous=((times[1:]-times[:-1])==interval*60_000_000_000)[:,None]
        mask=continuous & valid[1:] & valid[:-1]
        if mask.any():
            centered=score-score.mean(dim=1,keepdim=True)
            base=base+.015*F.huber_loss((centered[1:]-centered[:-1])[mask],torch.zeros_like(centered[1:][mask]))
    return base


def fit_trial(trial,panel,columns,train,cfg):
    y=target(train,panel.factors,trial.label)
    valid=np.isfinite(y)
    prep=Preprocessor.fit(train["x"][:,:,columns][valid])
    x=transform(prep,panel,columns,trial.family)
    y_scale=max(float(np.nanstd(y)),1e-8) if trial.label in ("raw","residual") else 1.
    time_ns=torch.from_numpy(train["times"].asi8.copy())
    started=perf_counter()
    logs=[]
    if trial.family in ("LightGBM","Ridge"):
        params={"alpha":10.,"num_leaves":15,"max_depth":4,"n_estimators":120}
        obj,_,_=fit_model(trial.family,params,x[train["selector"]],y/y_scale,None,{**cfg,"seed":trial.seed})
    else:
        torch.manual_seed(trial.seed)
        rng=np.random.default_rng(trial.seed)
        obj=construct_v3(trial.family,len(columns),len(panel.symbols))
        opt=torch.optim.AdamW(obj.parameters(),lr=.001)
        xt=torch.from_numpy(x[train["selector"]])
        yt=torch.from_numpy(np.nan_to_num(y/y_scale).astype(np.float32))
        masks=torch.from_numpy(valid)
        available=torch.from_numpy(train["eligible"])
        raw=train["raw"]
        raw_scale=max(float(np.nanstd(raw)),1e-8)
        raw_t=torch.from_numpy(np.nan_to_num(raw/raw_scale).astype(np.float32))
        raw_mask=torch.from_numpy(np.isfinite(raw))
        blocks=[slice(i,min(len(xt),i+128)) for i in range(0,len(xt),128)]
        for epoch in range(1,cfg["epochs"]+1):
            obj.train()
            losses=[]
            for idx in rng.permutation(len(blocks)):
                sl=blocks[idx]
                if not masks[sl].any():
                    continue
                out=obj(xt[sl],available[sl])
                loss=loss_function(trial.objective,out,yt[sl],masks[sl],raw_t[sl],raw_mask[sl],time_ns[sl],trial.horizon)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(obj.parameters(),5.)
                opt.step()
                losses.append(float(loss.detach()))
            logs.append((epoch,float(np.mean(losses))))
    return obj,prep,x,perf_counter()-started,logs


def score_view(model,family,prepared,view):
    x=prepared[view["selector"]]
    f=prediction(model,family,x) if family in ("LightGBM","Ridge") else predict(model,x,view["eligible"],"cpu")
    return np.where(view["eligible"],f,np.nan)
