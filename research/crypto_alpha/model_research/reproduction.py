"""Run trusted pinned source units and catalogue adaptations."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import torch
from torch import nn

from model_research.model import original_stockmixer_loss
from training.reproduce import load_functions
from v2.networks import PrefixMixer
from v2.reproduce import upstream
from v3.common import ROOT,digest


REPOS={
 "Ridge":("scikit-learn","1.9.1 installed","closed form L2 linear regression"),
 "LightGBM":("microsoft/qlib; vnpy/vnpy","54355232463878d2eebb91fe0ee5fa7fa1f5976c; c6e231caf32b7fc97e6459817fff66458cf7e7c4","LightGBM regression with 15 leaves and depth 4"),
 "MLP":("vnpy/vnpy","c6e231caf32b7fc97e6459817fff66458cf7e7c4","64-ReLU-dropout(0.1)-32-ReLU-1"),
 "GRU":("PyTorch","2.15.0 installed","single GRU, hidden 16, last state and linear head"),
 "StockMixer":("SJTU-DMTai/StockMixer","cce13598afd3ff33ae317700a85ae08db0554652","12 channels, two triangular mixing scales, asset MLP 12"),
 "MASTER":("SJTU-DMTai/MASTER","de8f58557096abde4216a701b35fc4368158d111","16 channels, temporal/asset attention with two heads each, causal mean-feature gate"),
}


def reproduce(out):
    torch.manual_seed(123)
    ref=ROOT/"references"
    sm=ref/"StockMixer/src/model.py"
    master=ref/"MASTER/master.py"
    mlp=ref/"vnpy/vnpy/alpha/model/models/mlp_model.py"
    q=ref/"qlib/qlib/contrib/model/gbdt.py"
    stock=upstream(sm)
    source=stock["StockMixer"](9,16,5,6,1)
    scores=source(torch.randn(9,16,5))
    y=torch.randn(9,1)*.01
    base=torch.ones(9,1)
    mask=torch.ones(9,1)
    mask[2]=0
    loss=stock["get_loss"](scores,y,base,mask,9,.1)
    loss[0].backward()
    assert scores.shape==(9,1) and torch.isfinite(loss[0])
    p=(scores.detach()-1).T
    adapted=original_stockmixer_loss(p,y.T,mask.T.bool())
    torch.testing.assert_close(adapted,loss[0].detach())
    tri=stock["TriU"](8)
    mix=PrefixMixer(8)
    with torch.no_grad():
        for i,node in enumerate(tri.triU):
            mix.weight[i,:i+1]=node.weight[0]
            mix.bias[i]=node.bias[0]
        fixture=torch.randn(9,12,8)
        delta=(tri(fixture)-mix(fixture)).abs().max().item()
        torch.testing.assert_close(tri(fixture),mix(fixture))
    full_master=upstream(master,("MASTERModel",))["MASTER"](158,256,4,2,.5,.5,158,221,5)
    result=full_master(torch.randn(9,8,221))
    result.square().mean().backward()
    assert result.shape==(9,) and torch.isfinite(result).all()
    original=load_functions(mlp,["MlpNetwork"],{"torch":torch,"nn":nn})["MlpNetwork"](input_size=83,hidden_sizes=(256,))
    output=original(torch.randn(9,83))
    assert output.numel()==9
    output.square().mean().backward()
    rows=[
          (sm,"StockMixer","ORIGINAL_REPRODUCTION","Original source forward/backward, 9×16×5 fixture; original stock data not used",source),
          (sm,"StockMixer TriU","ARCHITECTURE_REPRODUCTION",f"Original vs vectorized TriU; maximum delta {delta:.3g}",mix),
          (sm,"StockMixer loss","ORIGINAL_REPRODUCTION","Source loss matched adapted return-output loss numerically; published signed pair term preserved",None),
          (master,"MASTER","ORIGINAL_REPRODUCTION","Original 158+63 inputs, hidden 256, 4/2 heads, CPU forward/backward; stock dataset not used",full_master),
          (mlp,"VeighNa MLP","ARCHITECTURE_REPRODUCTION","Original MlpNetwork default 256; CPU forward/backward; original training wrapper reviewed",original),
          (q,"Qlib LGBModel","SOURCE_REVIEWED","Read fitter and benchmark YAML; native LightGBM used with common raw IC early stop; existing Qlib stock benchmark in Foundation archive",None)]
    mapped=[]
    for file,name,scope,result,obj in rows:
        family="StockMixer" if name.startswith("StockMixer") else "MASTER" if name=="MASTER" else "MLP" if name.startswith("VeighNa") else "LightGBM"
        mapped.append(dict(model=name,source_repository=REPOS[family][0],source_commit=REPOS[family][1],
                           status=scope,source_file=str(file.relative_to(ref)),source_sha256=digest(file),
                           result=result,fixture_parameters=sum(x.numel() for x in obj.parameters()) if obj else None))
    for family,(repo,sha,arch) in REPOS.items():
        mapped.append(dict(model=family,source_repository=repo,source_commit=sha,
                      status="LIGHTWEIGHT_VARIANT" if family in {"StockMixer","MASTER"} else "CRYPTO_ADAPTATION",
                      result=arch))
    pd.DataFrame(mapped).to_csv(out/"model_reproduction.csv",index=False)


def registry(out,table,cfg):
    rows=[]
    for r in table.itertuples():
        repo,commit,architecture=REPOS[r.family]
        f=int(r.factor_set.split("_")[-1])
        length=cfg["lookback"] if r.family in {"GRU","StockMixer","MASTER"} else 1
        model_objective={"MSE":"MSE","IC":"1 - mean timestamp Pearson IC",
                        "MASTER_ORIGINAL_MSE":"source masked MSE on common untrimmed cohort",
                        "StockMixer_ORIGINAL":"source ratio-space MSE + 0.1 signed pair term"}[r.objective]
        rows.append(dict(model_id=r.trial,source_repository=repo,source_commit=commit,
               architecture=architecture,variant=r.variant,input_shape=f"sections×9×{length}×{f}" if length>1 else f"sections×9×{f}",
               output_shape="sections×9",parameter_count=r.parameter_count,
               parameter_count_definition="median across folds; leaf scores for LightGBM",
               lookback=length,training_objective=model_objective,label=r.label,optimizer="AdamW" if r.family not in {"Ridge","LightGBM"}
               else ("closed-form sklearn" if r.family=="Ridge" else "gradient boosting"),learning_rate=cfg["learning_rate"] if r.family not in {"Ridge","LightGBM"}
               else (.03 if r.family=="LightGBM" else None),batch_size=cfg["batch_sections"] if r.family not in {"Ridge","LightGBM"} else None,
               epochs=cfg["lgb_rounds"] if r.family=="LightGBM" else cfg["max_epochs"] if r.family!="Ridge" else None,
               early_stopping=("none" if r.family=="Ridge" else f"validation raw Pearson IC; patience {25 if r.family=='LightGBM' else cfg['patience']}"),
               random_seed=r.seed,device="CPU",status="LIGHTWEIGHT_VARIANT" if r.family in {"StockMixer","MASTER"} else "CRYPTO_ADAPTATION"))
    pd.DataFrame(rows).to_csv(out/"model_registry.csv",index=False)
