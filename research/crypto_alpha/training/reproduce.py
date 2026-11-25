"""Load only the reviewed reference functions for independent numeric parity."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from typing import Tuple, Union

import numpy as np
import pandas as pd
from scipy import stats

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from factors.library import daily_core
from portfolio.weights import weights
from training.metrics import section_ic


def load_functions(path, names, env):
    tree=ast.parse(path.read_text(encoding="utf-8"))
    found=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in names]
    if set(names)!={node.name for node in found}:
        raise ValueError(f"source functions changed: {path}")
    exec(compile(ast.Module(body=found,type_ignores=[]),str(path),"exec"),env)
    return env


def environment():
    return {"pd":pd,"np":np,"stats":stats,"warnings":__import__("warnings"),"re":re,
            "Union":Union,"Tuple":Tuple}


def quantile_panel(raw, bins):
    per_section=raw.stack().rename("raw").to_frame()
    values=bins.stack().rename("quantile")
    result=per_section.join(values).dropna()
    result["raw"]-=result.groupby(level=0).raw.transform("mean")
    by_time=result.groupby(["quantile",result.index.get_level_values(0)]).raw.mean()
    bottom=set()
    turns={}
    for time, frame in bins.iterrows():
        current=set(frame.index[frame==2])
        turns[time]=len(current-bottom)/len(current) if bottom and current else np.nan
        bottom=current
    return by_time,pd.Series(turns,name=2)


def parity(out):
    ref=ROOT/"references"
    dates=pd.date_range("2024-01-01",periods=4)
    assets=list("ABCDEF")
    rng=np.random.default_rng(73)
    x=rng.normal(size=(4,6))
    x[2,2]=x[2,3]
    y=rng.normal(size=(4,6))*.01
    dates_and_assets=pd.MultiIndex.from_product([dates,assets],names=["date","asset"])
    raw_frame=pd.DataFrame(y,index=dates,columns=assets)
    ref_frame=pd.DataFrame({"factor":x.ravel(),"1h":y.ravel(),
                            "factor_quantile":np.where(x>=np.median(x,axis=1)[:,None],2,1).ravel()},
                           index=dates_and_assets)
    utils=load_functions(ref/"alphalens/alphalens/utils.py",
                         ["get_forward_returns_columns","demean_forward_returns"],environment())
    env={**environment(),"utils":SimpleNamespace(**utils)}
    al=load_functions(ref/"alphalens/alphalens/performance.py",
                     ["factor_information_coefficient","factor_weights","factor_returns","mean_return_by_quantile","quantile_turnover"],env)
    upstream_weights=al["factor_weights"]
    def compatible_weights(*args,**kwargs):
        result=upstream_weights(*args,**kwargs)
        return result.droplevel(0) if result.index.nlevels==3 else result
    al["factor_weights"]=compatible_weights
    ric=section_ic(x,y,3,True)[0]
    np.testing.assert_allclose(ric,al["factor_information_coefficient"](ref_frame)["1h"])
    ours=np.stack([weights(r,"raw",1.,1.,3)[1] for r in x])
    rank=np.apply_along_axis(stats.rankdata,1,x)
    ranked=ref_frame.assign(factor=rank.ravel())
    for flavor,values,f in (("raw",ours,ref_frame),
                            ("rank",np.stack([weights(r,"rank",1.,1.,3)[1] for r in x]),ranked),
                            ("equal",np.stack([weights(r,"equal",1.,1.,3)[1] for r in x]),ref_frame)):
        equal=flavor=="equal"
        np.testing.assert_allclose(values.ravel(),al["factor_weights"](f,equal_weight=equal).reindex(dates_and_assets))
        np.testing.assert_allclose((values*y).sum(axis=1),al["factor_returns"](f,equal_weight=equal)["1h"])
    own_q,own_turn=quantile_panel(raw_frame,pd.DataFrame(ref_frame.factor_quantile.to_numpy().reshape(x.shape),index=dates,columns=assets))
    ref_q,_=al["mean_return_by_quantile"](ref_frame,by_date=True)
    np.testing.assert_allclose(own_q.sort_index(),ref_q["1h"].sort_index())
    np.testing.assert_allclose(own_turn,al["quantile_turnover"](ref_frame.factor_quantile,2))
    p=ref_frame.factor.rename_axis(index=["datetime","instrument"])
    label=ref_frame["1h"].rename_axis(index=p.index.names)
    qlib=load_functions(ref/"qlib/qlib/contrib/eva/alpha.py",["calc_ic"],environment())
    q_ic,q_ric=qlib["calc_ic"](p,label)
    np.testing.assert_allclose(section_ic(x,y,3)[0],q_ic)
    np.testing.assert_allclose(ric,q_ric)
    auth=load_functions(ref/"qlib/qlib/utils/data.py",["zscore"],environment())
    processors=load_functions(ref/"qlib/qlib/data/dataset/processor.py",["CSRankNorm","CSZScoreNorm"],
                              {**environment(),"Processor":object,"zscore":auth["zscore"],
                               "get_group_columns":lambda f,_: f.columns})
    frame=p.to_frame()
    rank_original=(frame.groupby("datetime").rank(pct=True)-.5)*3.46
    raw_original=(frame-frame.groupby("datetime").transform("mean"))/frame.groupby("datetime").transform("std")
    pd.testing.assert_frame_equal(processors["CSRankNorm"]()(frame.copy()),rank_original)
    pd.testing.assert_frame_equal(processors["CSZScoreNorm"]()(frame.copy()),raw_original)
    qload=load_functions(ref/"qlib/qlib/contrib/data/loader.py",["Alpha158DL","Alpha360DL"],
                         {"QlibDataLoader":object})
    expressions,names=qload["Alpha158DL"].get_feature_config()
    expr360,names360=qload["Alpha360DL"].get_feature_config()
    assert (len(names),len(names360))==(158,360)
    minute=pd.date_range("2024-01-01 00:01",periods=62*1440,freq="min",tz="UTC")
    c=100.+np.cumsum(rng.normal(scale=.01,size=len(minute)))
    data=pd.DataFrame({"open":c-.03,"high":c+.3,"low":c-.4,"close":c,
                       "volume":2.,"quote_volume":c*2},index=minute)
    core=daily_core(data)
    assert set(core.columns)<=set(names)
    daily=data.resample("1D",closed="right",label="right").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum","quote_volume":"sum"})
    daily["vwap"]=daily.quote_volume/daily.volume
    def evaluate(expr):
        expr=re.sub(r"\$([a-z]+)",r"\1",expr)
        return eval(expr,{"__builtins__":{}},dict(**daily.to_dict("series"),
                    Ref=lambda v,w:v.shift(w),Mean=lambda v,w:v.rolling(w).mean(),
                    Std=lambda v,w:v.rolling(w).std(),Max=lambda v,w:v.rolling(w).max(),
                    Min=lambda v,w:v.rolling(w).min(),Greater=np.maximum,Less=np.minimum))
    for name,expr in zip(names,expressions):
        if name in core:
            np.testing.assert_allclose(core[name],evaluate(expr),equal_nan=True)
    normalized=np.column_stack([evaluate(expr).to_numpy() for expr in expr360])
    last=np.flatnonzero(daily.close.notna())[-1]
    np.testing.assert_allclose(normalized[last,[0,59,60,359]],
                               [daily.close.iloc[last-59]/daily.close.iloc[last],1,
                                daily.open.iloc[last-59]/daily.close.iloc[last],1])
    np.testing.assert_allclose(evaluate("Ref($close, -2)/Ref($close, -1) - 1"),
                               daily.close.shift(-2)/daily.close.shift(-1)-1,equal_nan=True)
    evidence={"alphalens_rank_ic_max_delta":float(np.max(abs(ric-al["factor_information_coefficient"](ref_frame)["1h"]))),
              "alphalens_weights_rank_raw_equal":True,"alphalens_factor_returns_quantile_turnover":True,
              "qlib_pearson_rank_ic":True,"qlib_rank_zscore_processors":True,
              "qlib_alpha158_formula_count":len(names),"qlib_alpha158_daily_core_numeric_count":len(core.columns),
              "qlib_alpha360_source_expressions_evaluated":normalized.shape[1],"qlib_daily_label":True}
    (out/"numeric_parity.json").write_text(json.dumps(evidence,indent=2),encoding="utf-8")
    print(json.dumps(evidence,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    parity(args.output)
