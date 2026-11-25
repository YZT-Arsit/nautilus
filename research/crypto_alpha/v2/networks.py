"""Compact source-inspired CPU/GPU architectures for complete time sections."""
import math
from copy import deepcopy

import numpy as np
import torch
from torch import nn

from models.cpu import ic_loss


def history(x, length):
    ext=np.concatenate([np.repeat(x[:1],length-1,axis=0),x],axis=0)
    windows=np.lib.stride_tricks.sliding_window_view(ext,length,axis=0)
    return np.ascontiguousarray(windows.transpose(0,1,3,2))


class GRU(nn.Module):
    def __init__(self, features):
        super().__init__()
        self.gru=nn.GRU(features,16,batch_first=True)
        self.output=nn.Linear(16,1)
    def forward(self,x,eligible):
        batch,assets,steps,features=x.shape
        h,_=self.gru(x.reshape(-1,steps,features))
        return self.output(h[:,-1]).reshape(batch,assets)


class PrefixMixer(nn.Module):
    """Vectorized affine prefix mixer with the triangular support of upstream TriU."""
    def __init__(self,steps):
        super().__init__()
        self.weight=nn.Parameter(torch.empty(steps,steps))
        self.bias=nn.Parameter(torch.zeros(steps))
        self.register_buffer("mask",torch.ones(steps,steps).tril())
        nn.init.xavier_uniform_(self.weight)
    def forward(self,x):
        return torch.nn.functional.linear(x,self.weight*self.mask,self.bias)


class MixerBlock(nn.Module):
    def __init__(self,steps,channels):
        super().__init__()
        self.temporal=PrefixMixer(steps)
        self.norm=nn.LayerNorm(channels)
        self.channel=nn.Sequential(nn.Linear(channels,channels),nn.GELU(),nn.Linear(channels,channels))
    def forward(self,x):
        t=x+self.temporal(self.norm(x).transpose(-1,-2)).transpose(-1,-2)
        return t+self.channel(self.norm(t))


class StockMixer(nn.Module):
    """Two time scales, channel mixing, and a fixed-universe stock MLP."""
    def __init__(self,features,assets,steps):
        super().__init__()
        self.embed=nn.Linear(features,12)
        self.fine=MixerBlock(steps,12)
        self.coarse=MixerBlock(steps//2,12)
        self.down=nn.Conv1d(12,12,2,stride=2)
        self.channel=nn.Linear(12,1)
        self.asset=nn.Sequential(nn.LayerNorm(assets),nn.Linear(assets,12),nn.Hardswish(),nn.Linear(12,assets))
        representation=2*steps+steps//2
        self.base=nn.Linear(representation,1)
        self.market=nn.Linear(representation,1)
    def forward(self,x,eligible):
        h=self.embed(x)
        b,a,t,d=h.shape
        coarse=self.down(h.reshape(-1,t,d).transpose(1,2)).transpose(1,2).reshape(b,a,t//2,d)
        u=self.channel(torch.cat([h,self.fine(h),self.coarse(coarse)],dim=2)).squeeze(-1)
        z=self.asset((u*eligible.unsqueeze(-1)).transpose(1,2)).transpose(1,2)
        return (self.base(u)+self.market(z)).squeeze(-1)


class AttentionBlock(nn.Module):
    def __init__(self,d):
        super().__init__()
        self.norm=nn.LayerNorm(d)
        self.attn=nn.MultiheadAttention(d,2,dropout=.1,batch_first=True)
        self.ffn=nn.Sequential(nn.LayerNorm(d),nn.Linear(d,d),nn.ReLU(),nn.Linear(d,d))
    def forward(self,x,mask=None):
        normalized=self.norm(x)
        y=x+self.attn(normalized,normalized,normalized,key_padding_mask=mask,need_weights=False)[0]
        return y+self.ffn(y)


class MASTER(nn.Module):
    """Market feature gate then temporal/asset attention and temporal pooling."""
    def __init__(self,features,steps):
        super().__init__()
        dim=16
        self.gate=nn.Linear(features,features)
        self.embed=nn.Linear(features,dim)
        pos=torch.arange(steps)[:,None]*torch.exp(torch.arange(0,dim,2)*(-math.log(10000)/dim))
        pe=torch.zeros(steps,dim)
        pe[:,0::2]=torch.sin(pos)
        pe[:,1::2]=torch.cos(pos)
        self.register_buffer("pe",pe)
        self.time=AttentionBlock(dim)
        self.asset=AttentionBlock(dim)
        self.query=nn.Linear(dim,dim,bias=False)
        self.output=nn.Linear(dim,1)
    def forward(self,x,eligible):
        b,a,t,_=x.shape
        market=(x[:,:,-1]*eligible[:,:,None]).sum(1)/eligible.sum(1,keepdim=True).clamp(min=1)
        gate=self.gate(market).softmax(dim=-1)*x.shape[-1]
        h=self.embed(x*gate[:,None,None,:])+self.pe
        d=h.shape[-1]
        h=self.time(h.reshape(b*a,t,d)).reshape(b,a,t,d)
        mask=~eligible
        mask=mask & (~mask.all(dim=1,keepdim=True))
        mask=mask[:,None,:].expand(b,t,a).reshape(b*t,a)
        z=self.asset(h.transpose(1,2).reshape(b*t,a,d),mask).reshape(b,t,a,d).transpose(1,2)
        u=self.query(z)
        alpha=(u*u[:,:,-1:]).sum(-1).softmax(-1)
        return self.output((z*alpha[:,:,:,None]).sum(2)).squeeze(-1)


def construct(family,num_features,num_assets,steps):
    if family=="GRU":
        return GRU(num_features)
    if family=="StockMixer":
        return StockMixer(num_features,num_assets,steps)
    return MASTER(num_features,steps)


def predict(model,x,eligible,device):
    model.eval()
    out=[]
    with torch.inference_mode():
        for first in range(0,len(x),256):
            sl=slice(first,first+256)
            out.append(model(torch.from_numpy(x[sl]).to(device),torch.from_numpy(eligible[sl]).to(device)).cpu().numpy())
    return np.concatenate(out)


def fit(family, objective, x,y, eligible, cfg, validation=None, epochs=None):
    torch.manual_seed(cfg["seed"])
    device=torch.device(cfg["device"])
    model=construct(family,x.shape[-1],x.shape[1],x.shape[2]).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=.001)
    rng=np.random.default_rng(cfg["seed"])
    truth=torch.from_numpy(np.nan_to_num(y).astype(np.float32))
    known=torch.from_numpy(np.isfinite(y))
    best=-np.inf
    best_epoch=0
    state=None
    history=[]
    from models.cpu import score
    for epoch in range(1,(epochs or cfg["epochs"])+1):
        model.train()
        ids=rng.permutation(len(x))
        losses=[]
        for i in range(0,len(ids),128):
            idx=ids[i:i+128]
            valid=known[idx].to(device)
            if not valid.any():
                continue
            p=model(torch.from_numpy(x[idx]).to(device),torch.from_numpy(eligible[idx]).to(device))
            q=truth[idx].to(device)
            loss=ic_loss(p,q,valid,cfg["min_assets"]) if objective=="IC" else (p[valid]-q[valid]).square().mean()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(),5.)
            opt.step()
            losses.append(float(loss.detach().cpu()))
        metric=None
        if validation is not None:
            inputs,raw,mask=validation
            metric=score(predict(model,inputs,mask,device),raw,mask,cfg["min_assets"])
            if metric>best:
                best,best_epoch,state=metric,epoch,deepcopy(model.state_dict())
        history.append(dict(epoch=epoch,loss=float(np.mean(losses)),validation_ic=metric))
        if validation is not None and epoch-best_epoch>=cfg["patience"]:
            break
    if state is not None:
        model.load_state_dict(state)
    return model,best_epoch if validation is not None else epochs,history
