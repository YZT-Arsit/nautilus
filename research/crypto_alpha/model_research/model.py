"""Common estimator interface for Foundation learners and compact V2 networks."""
from copy import deepcopy
from dataclasses import asdict,dataclass
import json

import joblib
import lightgbm
import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.nn import functional as F

from models.cpu import Preprocessor, fit_model, prediction, ic_loss, score
from training.metrics import section_ic,score_summary
from v2.networks import construct,history
from v3.objectives import SectionMLP
from v3.common import write_json

TEMPORAL={"GRU","StockMixer","MASTER"}


@dataclass(frozen=True)
class Trial:
    family:str
    objective:str
    label:str="rank"
    factor_set:str="NO_DERIVATIVES_83"
    variant:str="full"
    interval:int=60
    seed:int=42
    scope:str="core"

    @property
    def key(self):
        return "_".join(map(str,[self.scope,self.family,self.objective,self.label,self.factor_set,self.variant,self.interval,self.seed]))


class Zero(nn.Module):
    def forward(self,x):
        return torch.zeros_like(x)


class ZeroHead(nn.Module):
    def forward(self,x):
        return torch.zeros_like(x[:,:,:1])


class MaskIdentity(nn.Module):
    def forward(self,x,mask=None):
        return x


def network(t,n,assets,steps):
    if t.family=="MLP":
        return SectionMLP(n)
    obj=construct(t.family,n,assets,steps)
    if t.family=="MASTER":
        if t.variant=="no_gate":
            obj.gate=Zero()
        if t.variant in ("no_time","no_time_no_asset"):
            obj.time=MaskIdentity()
        if t.variant in ("no_asset","no_time_no_asset"):
            obj.asset=MaskIdentity()
    if t.family=="StockMixer":
        if t.variant=="no_factor_mix":
            obj.fine.channel=obj.coarse.channel=Zero()
        if t.variant=="no_time_mix":
            obj.fine.temporal=obj.coarse.temporal=Zero()
            obj.down=nn.AvgPool1d(2)
        if t.variant=="no_asset_mix":
            obj.asset=nn.Identity()
            obj.market=ZeroHead()
    return obj


def original_stockmixer_loss(pred,y,mask,alpha=.1):
    """Upstream signed pair term, applied within each section in return units."""
    p=torch.where(mask,pred,0.)
    yy=torch.where(mask,y,0.)
    mse=(p-yy).square().mean()
    dp=p[:,:,None]-p[:,None,:]
    dy=yy[:,:,None]-yy[:,None,:]
    valid=mask[:,:,None]&mask[:,None,:]
    return mse + alpha*F.relu(dp*dy*valid).mean()


def prepared_label(view,label):
    # Same information and supervision membership across raw and rank comparisons.
    normalized=view["labels"]["label_rank_zscore"]
    return normalized if label=="rank" else np.where(np.isfinite(normalized),view["raw"],np.nan)


def evaluate(pred,view,label,minimum):
    truth=prepared_label(view,label)
    raw=section_ic(pred,view["raw"],minimum)[0]
    rank=section_ic(pred,view["raw"],minimum,True)[0]
    processed=section_ic(pred,truth,minimum)[0]
    p,r,y=map(score_summary,(raw,rank,processed))
    return dict(ic=p["mean"],rank_ic=r["mean"],icir=p["ir"],processed_ic=y["mean"],
                positive_ic_ratio=p["positive_ratio"],valid_sections=p["valid_sections"]),pd.DataFrame(
        dict(prediction_time=view["times"],ic=raw,rank_ic=rank,processed_ic=processed))


class Model:
    def __init__(self, trial, cfg, columns):
        self.trial=trial
        self.cfg=cfg
        self.columns=list(columns) # ordered feature names
        self.steps=cfg["lookback"] if trial.family in TEMPORAL else 1
        self.logs=[]

    def _get(self,panel,view):
        pos=[panel.factors.index(c) for c in self.columns]
        selected = np.flatnonzero(view["selector"])
        first = max(0, selected[0]-self.steps+1)
        last = selected[-1]+1
        x = self.prep.transform(panel.x[first:last,:,pos])
        x = np.where(panel.eligible[first:last,:,None],x,0.).astype(np.float32,copy=False)
        if self.steps>1:
            x = history(x,self.steps)
        return x[selected-first]

    def _out(self,x,m):
        if self.trial.family in ("Ridge","LightGBM"):
            return prediction(self.obj,self.trial.family,x)
        self.obj.eval()
        with torch.inference_mode():
            return np.concatenate([self.obj(torch.from_numpy(x[i:i+256]),torch.from_numpy(m[i:i+256])).numpy()
                                   for i in range(0,len(x),256)])

    def fit(self,panel,train,val=None,rounds=None):
        t=self.trial
        c=self.cfg
        y=prepared_label(train,t.label)
        mask=np.isfinite(y)
        pos=[panel.factors.index(k) for k in self.columns]
        self.prep=Preprocessor.fit(train["x"][:,:,pos][mask])
        self.scale=max(float(np.nanstd(y)),1e-8) if t.label=="raw" else 1.
        x=self._get(panel,train)
        vx=self._get(panel,val) if val is not None else None
        if t.family in ("Ridge","LightGBM"):
            args=dict(alpha=1.,num_leaves=15,max_depth=4,n_estimators=c["lgb_rounds"])
            watch=(vx,val["raw"],val["eligible"]) if val is not None else None
            self.obj,self.rounds,logs=fit_model(t.family,args,x,y/self.scale,watch,
                                                {**c,"seed":t.seed,"threads":c["num_threads"]},rounds)
            self.logs=[dict(iteration=i,loss=l,validation_ic=v) for i,l,v in logs]
            self.params=int(self.obj.coef_.size+1) if t.family=="Ridge" else sum(
                tree["num_leaves"] for tree in self.obj.dump_model()["tree_info"])
            return self
        torch.manual_seed(t.seed)
        rng=np.random.default_rng(t.seed)
        self.obj=network(t,len(self.columns),len(panel.symbols),self.steps)
        self.params=sum(p.numel() for p in self.obj.parameters())
        opt=torch.optim.AdamW(self.obj.parameters(),lr=c["learning_rate"])
        best=-np.inf
        state=None
        self.rounds=rounds or c["max_epochs"]
        tx=torch.from_numpy(x)
        ty=torch.from_numpy(np.nan_to_num(y/self.scale).astype(np.float32))
        masks=torch.from_numpy(mask)
        available=torch.from_numpy(train["eligible"])
        for epoch in range(1,self.rounds+1):
            self.obj.train()
            losses=[]
            order=rng.permutation(len(x))
            for start in range(0,len(x),c["batch_sections"]):
                ix=order[start:start+c["batch_sections"]]
                valid=masks[ix]
                if not valid.any():
                    continue
                p=self.obj(tx[ix],available[ix])
                yy=ty[ix]
                if t.objective=="IC":
                    loss=ic_loss(p,yy,valid,c["min_assets"])
                elif t.objective=="StockMixer_ORIGINAL":
                    loss=original_stockmixer_loss(p,yy,valid)
                else:
                    loss=F.mse_loss(p[valid],yy[valid])
                if not torch.isfinite(loss):
                    raise ValueError(f"nonfinite loss in {t.key}")
                opt.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(self.obj.parameters(),5.)
                opt.step()
                losses.append(float(loss.detach()))
            ic=score(self._out(vx,val["eligible"]),val["raw"],val["eligible"],c["min_assets"]) if val is not None else None
            self.logs.append(dict(iteration=epoch,loss=float(np.mean(losses)),validation_ic=ic))
            if val is not None and ic>best:
                best,self.rounds,state=ic,epoch,deepcopy(self.obj.state_dict())
            if state is not None and epoch-self.rounds>=c["patience"]:
                break
        if state is not None:
            self.obj.load_state_dict(state)
        return self

    def predict(self,panel,view):
        return np.where(view["eligible"],self._out(self._get(panel,view),view["eligible"]),np.nan)

    def evaluate(self,score,view):
        return evaluate(score,view,self.trial.label,self.cfg["min_assets"])

    def save(self,path,assets):
        path.mkdir(parents=True,exist_ok=True)
        joblib.dump(self.prep,path/"scaler.joblib")
        if self.trial.family=="LightGBM":
            self.obj.save_model(str(path/"model"))
        elif self.trial.family=="Ridge":
            joblib.dump(self.obj,path/"model")
        else:
            torch.save(self.obj.state_dict(),path/"model")
        write_json(path/"model.json",dict(configuration=asdict(self.trial),features=self.columns,
            feature_order=self.columns,rounds=self.rounds,parameters=self.params,assets=assets,steps=self.steps,
            raw_target_scale=self.scale))

    @classmethod
    def load(cls,path,cfg):
        data=json.loads((path/"model.json").read_text())
        t=Trial(**data["configuration"])
        item=cls(t,cfg,data["features"])
        item.prep=joblib.load(path/"scaler.joblib")
        item.params,item.rounds,item.scale=data["parameters"],data["rounds"],data["raw_target_scale"]
        if t.family=="LightGBM":
            item.obj=lightgbm.Booster(model_file=str(path/"model"))
        elif t.family=="Ridge":
            item.obj=joblib.load(path/"model")
        else:
            item.obj=network(t,len(item.columns),data["assets"],item.steps)
            item.obj.load_state_dict(torch.load(path/"model",map_location="cpu",weights_only=True))
        return item
