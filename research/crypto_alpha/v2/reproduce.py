"""Execute reviewed upstream architecture units from pinned local source trees."""
import argparse
import ast
import json
import math
from pathlib import Path
import subprocess

import torch
from torch import nn
from torch.nn import Dropout, LayerNorm, Linear
from torch.nn import functional as F

from v2.common import ROOT
from v2.networks import PrefixMixer


def upstream(path,ignore=()):
    tree=ast.parse(path.read_text(encoding="utf-8"))
    nodes=[node for node in tree.body if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.Assign))
           and getattr(node,"name","") not in ignore]
    scope=dict(torch=torch,nn=nn,F=F,math=math,Linear=Linear,LayerNorm=LayerNorm,Dropout=Dropout)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),"exec"),scope)
    return scope


def verify(out):
    torch.manual_seed(42)
    smroot=ROOT/"references/StockMixer"
    maroot=ROOT/"references/MASTER"
    stock=upstream(smroot/"src/model.py")
    master=upstream(maroot/"master.py",("MASTERModel",))
    tri=stock["TriU"](8)
    ours=PrefixMixer(8)
    with torch.no_grad():
        for i,step in enumerate(tri.triU):
            ours.weight[i,:i+1]=step.weight[0]
            ours.bias[i]=step.bias[0]
        tensor=torch.randn(9,12,8)
        diff=(tri(tensor)-ours(tensor)).abs().max().item()
        torch.testing.assert_close(tri(tensor),ours(tensor))
    regressor=stock["StockMixer"](9,16,12,6,1)
    output=regressor(torch.randn(9,16,12))
    result=stock["get_loss"](output,torch.randn(9,1)*.01,torch.ones(9,1),torch.ones(9,1),9,.1)
    result[0].backward()
    assert output.shape==(9,1) and torch.isfinite(result[0])
    mod=master["MASTER"](12,16,2,2,.1,.1,12,16,1)
    o=mod(torch.randn(9,8,16))
    o.square().mean().backward()
    assert o.shape==(9,) and torch.isfinite(o).all()
    data={"status":"UNIT_REPRODUCED",
          "stockmixer_original_forward_backward":"PASS","stockmixer_triu_max_delta":diff,
          "master_original_forward_backward":"PASS","stockmixer_shape":list(output.shape),"master_shape":list(o.shape),
          "stockmixer_commit":subprocess.check_output(["git","-C",str(smroot),"rev-parse","HEAD"],text=True).strip(),
          "master_commit":subprocess.check_output(["git","-C",str(maroot),"rev-parse","HEAD"],text=True).strip(),
          "scope":"Upstream source units on deterministic tensors, V2 training separate"}
    out.write_text(json.dumps(data,indent=2))
    print(json.dumps(data))


if __name__=="__main__":
    arg=argparse.ArgumentParser()
    arg.add_argument("--output",type=Path,required=True)
    verify(arg.parse_args().output)
