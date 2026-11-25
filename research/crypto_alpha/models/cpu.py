"""CPU estimators with validation driven by per-section raw-return IC."""
from copy import deepcopy
from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
from sklearn.linear_model import Ridge
import torch
from torch import nn

from training.metrics import section_ic


@dataclass
class Preprocessor:
    median: np.ndarray
    low: np.ndarray
    high: np.ndarray
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, x):
        lo, median, hi = np.nanquantile(x, [.005, .5, .995], axis=0)
        filled = np.clip(np.where(np.isfinite(x), x, median), lo, hi)
        scale = filled.std(axis=0)
        return cls(median, lo, hi, filled.mean(axis=0), np.where(scale > 1e-12, scale, 1.))

    def transform(self, x):
        filled = np.clip(np.where(np.isfinite(x), x, self.median), self.low, self.high)
        return ((filled-self.mean)/self.scale).astype(np.float32)


def net(features):
    return nn.Sequential(nn.Linear(features,64),nn.ReLU(),nn.Dropout(.1),
                         nn.Linear(64,32),nn.ReLU(),nn.Linear(32,1))


def ic_loss(pred, label, valid, minimum=3, epsilon=1e-6):
    count = valid.sum(dim=1).clamp(min=1)
    x=torch.where(valid,pred,0.)
    y=torch.where(valid,label,0.)
    x=torch.where(valid,x-x.sum(dim=1,keepdim=True)/count[:,None],0.)
    y=torch.where(valid,y-y.sum(dim=1,keepdim=True)/count[:,None],0.)
    xx=(x*x).sum(dim=1)
    yy=(y*y).sum(dim=1)
    usable=(count>=minimum)&(yy>epsilon**2)
    if not usable.any():
        return pred.sum()*0.
    # The clamp gives a finite gradient even if all scores start identical.
    scale=(xx.clamp_min(epsilon**2)*yy.clamp_min(epsilon**2)).sqrt()
    return 1-((x*y).sum(dim=1)/scale)[usable].mean()


def score(pred, raw, eligible, minimum):
    values, _=section_ic(np.where(eligible,pred,np.nan),raw,minimum)
    return np.nanmean(values) if np.isfinite(values).any() else -np.inf


def torch_prediction(model,x):
    model.eval()
    with torch.inference_mode():
        return np.concatenate([model(torch.from_numpy(block)).squeeze(-1).numpy()
                               for block in np.array_split(x,max(1,len(x)//1024))])


def fit_nn(x,y,validation,cfg,objective,epochs=None):
    torch.manual_seed(cfg["seed"])
    rng=np.random.default_rng(cfg["seed"])
    model=net(x.shape[-1])
    opt=torch.optim.AdamW(model.parameters(),lr=.001)
    tx=torch.from_numpy(x)
    ty=torch.from_numpy(np.nan_to_num(y).astype(np.float32))
    mask=torch.from_numpy(np.isfinite(y))
    model.train()
    best, best_epoch, state=-np.inf, 0, None
    history=[]
    for epoch in range(1,(epochs or cfg["epochs"])+1):
        model.train()
        losses=[]
        order=rng.permutation(len(x))
        for start in range(0,len(order),128):
            section=order[start:start+128]
            predicted=model(tx[section]).squeeze(-1)
            target=ty[section]
            valid=mask[section]
            if not valid.any():
                continue
            loss=(ic_loss(predicted,target,valid,cfg["min_assets"]) if objective=="MLP-IC"
                  else (predicted[valid]-target[valid]).square().mean())
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(),5.)
            opt.step()
            losses.append(float(loss.detach()))
        if validation is None:
            history.append((epoch,float(np.mean(losses)),None))
            continue
        val_x,val_raw,val_mask=validation
        metric=score(torch_prediction(model,val_x),val_raw,val_mask,cfg["min_assets"])
        history.append((epoch,float(np.mean(losses)),float(metric)))
        if metric>best+1e-8:
            best,best_epoch,state=metric,epoch,deepcopy(model.state_dict())
        if epoch-best_epoch>=cfg["patience"]:
            break
    if state is not None:
        model.load_state_dict(state)
    return model, best_epoch if validation is not None else epochs, history


def fit_model(family, params, x, y, validation, cfg, rounds=None):
    has_label = np.isfinite(y)
    if family.startswith("MLP"):
        return fit_nn(x,y,validation,cfg,family,rounds)
    if family=="Ridge":
        estimator=Ridge(alpha=params["alpha"]).fit(x[has_label],y[has_label])
        return estimator,None,[]
    seed = cfg["seed"]
    lgb_params={"objective":"regression","metric":"None","learning_rate":.03,
                "num_leaves":params["num_leaves"],"max_depth":params["max_depth"],
                "min_data_in_leaf":50,"feature_fraction":.8,"seed":seed,
                "device_type":"cpu","num_threads":cfg["threads"],"deterministic":True,
                "force_col_wise":True,"verbosity":-1}
    train=lgb.Dataset(x[has_label],label=y[has_label])
    evaluation={}
    kwargs={}
    if validation is not None:
        val_x,val_raw,val_mask=validation
        val_mask=val_mask & np.isfinite(val_raw)
        valid = lgb.Dataset(val_x[val_mask],label=val_raw[val_mask],reference=train)
        # Early stopping follows the same ex-ante selection criterion as Ridge and MLP.
        def metric(flat, _dataset):
            expanded=np.full(val_raw.shape,np.nan)
            expanded[val_mask]=flat
            return "raw_pearson_ic",float(score(expanded,val_raw,val_mask,cfg["min_assets"])),True
        kwargs={"valid_sets":[valid],"valid_names":["validation"],"feval":metric,
                "callbacks":[lgb.early_stopping(25,verbose=False),lgb.record_evaluation(evaluation)]}
    model=lgb.train(lgb_params,train,num_boost_round=rounds or params["n_estimators"],**kwargs)
    history=[(i+1,None,float(v)) for i,v in enumerate(evaluation.get("validation",{}).get("raw_pearson_ic",[]))]
    return model,model.best_iteration or rounds or params["n_estimators"],history


def prediction(model,family,x):
    if family.startswith("MLP"):
        return torch_prediction(model,x)
    flat=x.reshape(-1,x.shape[-1])
    scores=model.predict(flat,num_threads=2) if family=="LightGBM" else model.predict(flat)
    return scores.reshape(x.shape[:2])
