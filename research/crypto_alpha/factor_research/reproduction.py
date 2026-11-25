"""Pinned daily receipts and direct source-level operator checks."""
import ast
from abc import ABCMeta, abstractmethod
from types import SimpleNamespace
from typing import Optional, Sequence, Tuple

import numpy as np
import numpy.typing as npt
import pandas as pd
import torch
from torch import Tensor

from training.metrics import section_ic
from training.reproduce import load_functions
from v3.common import ROOT, digest, write_json


def compare(out, reg):
    root = ROOT/"references"
    file = root/"factor_research_parity/upstream_daily_parity.csv"
    daily = pd.read_csv(file)
    assert daily.groupby("project").size().to_dict()=={"Qlib Alpha158":158,"VeighNa Alpha158":158}
    assert len(daily.query("project=='Qlib Alpha158' and status=='NUMERICALLY_REPRODUCED'"))==158
    source = root/"alphagen/alphagen"
    env = dict(torch=torch,Tensor=Tensor,Optional=Optional,Tuple=Tuple,Sequence=Sequence,
               ABCMeta=ABCMeta,abstractmethod=abstractmethod,Expression=object)
    load_functions(source/"utils/pytorch_utils.py",["masked_mean_std"],env)
    load_functions(source/"utils/correlation.py",["_mask_either_nan","_rank_data_1d","_rank_data",
                  "_batch_pearsonr_given_mask","batch_spearmanr","batch_pearsonr"],env)
    load_functions(source/"data/calculator.py",["AlphaCalculator","TensorAlphaCalculator"],env)
    class Fixture(env["TensorAlphaCalculator"]):
        def evaluate_alpha(self, expression):
            return expression

        @property
        def n_days(self):
            return len(self.target)

    rng = np.random.default_rng(197)
    x,y,z = rng.normal(size=(3,7,9))
    x[4,0]=np.nan
    T = torch.from_numpy
    engine = Fixture(T(y))
    p,r = engine.calc_single_all_ret(T(x))
    a = engine.calc_mutual_IC(T(x),T(z))
    cp,cr = engine.calc_pool_all_ret([T(x),T(z)],[.3,.7])
    check = np.array([p,r,a,cp,cr])
    own = np.array([np.mean(section_ic(e,t,3,rank)[0]) for e,t,rank in (
        (x,y,False),(x,y,True),(x,z,False),(.3*x+.7*z,y,False),(.3*x+.7*z,y,True))])
    np.testing.assert_allclose(check,own,atol=1e-12)
    freq = root/"freqtrade/freqtrade/freqai/data_kitchen.py"
    parsed = ast.parse(freq.read_text(encoding="utf-8"))
    cls = next(n for n in parsed.body if isinstance(n,ast.ClassDef) and n.name=="FreqaiDataKitchen")
    methods = [n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in
               {"find_features","find_labels","set_weights_higher_recent"}]
    scope = dict(DataFrame=pd.DataFrame,npt=npt,np=np,OperationalException=ValueError)
    exec(compile(ast.Module(body=methods,type_ignores=[]),str(freq),"exec"),scope)
    obj = SimpleNamespace(config={"freqai":{"feature_parameters":{"weight_factor":.5}}})
    data = pd.DataFrame({"%-return":[1.],"&-future":[0.],"close":[90.]})
    scope["find_features"](obj,data)
    scope["find_labels"](obj,data)
    assert obj.training_features_list==["%-return"] and obj.label_list==["&-future"]
    n=7
    expect = np.array([np.exp(-(n-1-i)/(.5*n)) for i in range(n)])
    np.testing.assert_allclose(scope["set_weights_higher_recent"](obj,n),expect)
    units = [
        dict(project="AlphaGen",factor=name,formula="mean of timestamp-wise asset correlation",
             status="NUMERICALLY_REPRODUCED",scope="operator unit with nondegenerate sections; Phase 2 invalid IC stays NaN",
             comparisons=7,maximum_abs_delta=abs(got-want),engine="PyTorch CPU",
             source_commit="259687e8f316994426416c530a94842a2fe6405e",
             implementation="alphagen/data/calculator.py; alphagen/utils/correlation.py")
        for name,got,want in zip(["single_IC","single_Rank_IC","mutual_IC","linear_pool_IC","linear_pool_Rank_IC"],check,own)]
    units.extend(dict(project="FreqAI",factor=name,formula=formula,status="NUMERICALLY_REPRODUCED",
                      scope="original operator unit; Foundation uses distinct registry and annual split",
                      comparisons=n if name=="recent_weights" else 2,maximum_abs_delta=0,
                      engine="pandas/NumPy",source_commit="7fcb509a434cfaa5e0943a7ccc005967cfd0b9d9",
                      implementation="freqtrade/freqai/data_kitchen.py")
                 for name,formula in [("feature_label_discovery","% features; & labels"),("recent_weights","exp(-age/(weight_factor*n))")])
    units.extend(dict(project="Crypto",factor=r.factor_name,formula=r.mathematical_formula,status="CRYPTO_ADAPTED",
                      scope="independent minute factor of V1/V2 or registered plugin",implementation=r.implementation)
                 for r in reg.itertuples())
    units.extend(dict(project="Crypto",factor=f,formula="",status="DATA_UNAVAILABLE",
                      scope="no verified fixed-fixture historical field") for f in ["premium","basis","open_interest"])
    units.append(dict(project="AlphaGen",factor="RL search",formula="",status="NOT_IMPLEMENTED",
                      scope="formula diagnostics; no RL training"))
    pd.concat([daily,pd.DataFrame(units)],ignore_index=True).to_csv(out/"factor_reproduction.csv",index=False)
    write_json(out/"upstream_evidence.json",dict(daily_csv_sha256=digest(file),
               qlib_numerical=158,veighna_numerical=int((daily.project.eq("VeighNa Alpha158")&
               daily.status.eq("NUMERICALLY_REPRODUCED")).sum()),
               qlib_max_abs_delta=daily.loc[daily.project.eq("Qlib Alpha158"),"maximum_abs_delta"].max(),
               alphagen_max_abs_delta=float(np.max(np.abs(check-own))),freqai_units=2))
    return daily
