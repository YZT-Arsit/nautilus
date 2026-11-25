"""Compare pinned upstream daily formulas with independent numeric operators."""
import argparse
import ast
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from numbers import Real
from pathlib import Path
import re
from typing import Union, cast

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pandas as pd
import polars as pl
from scipy import stats
from qlib import __version__ as qlib_version
from qlib.data import ops

ROOT = Path(__file__).resolve().parents[1]


def source(path, scope, nodes=None, imports=False):
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    body = [node for node in parsed.body if (
        (isinstance(node,(ast.FunctionDef,ast.ClassDef)) and (nodes is None or node.name in nodes))
        or (imports and isinstance(node,(ast.Assign,ast.AnnAssign))))]
    exec(compile(ast.Module(body=body,type_ignores=[]),str(path),"exec"),scope)
    return scope


def fixture():
    rng = np.random.default_rng(8421)
    close = 40+np.cumsum(rng.normal(.02,.2,260))
    opening = close+rng.normal(0,.25,len(close))
    high = np.maximum(close,opening)+rng.uniform(.02,1,len(close))
    low = np.minimum(close,opening)-rng.uniform(.02,.6,len(close))
    return pd.DataFrame(dict(open=opening,close=close,high=high,low=low,
                             vwap=(close+opening+high+low)/4,volume=rng.lognormal(9,1,len(close))))


def independent(formula, daily):
    def regression(s,n,kind):
        def measure(y):
            t = np.arange(len(y),dtype=float)
            t -= t.mean()
            dy = y-y.mean()
            slope = (t@dy)/(t@t)
            if kind=="slope":
                return slope
            if kind=="rsquare":
                return (t@dy)**2 / ((t@t)*(dy@dy))
            return y[-1]-y.mean()-slope*t[-1]
        return s.rolling(n,min_periods=2).apply(measure,raw=True)
    rolling = lambda s,n:s.rolling(n,min_periods=1)
    env = dict(Ref=lambda s,n:s.shift(n),Mean=lambda s,n:rolling(s,n).mean(),
        Std=lambda s,n:rolling(s,n).std(ddof=1),Max=lambda s,n:rolling(s,n).max(),
        Min=lambda s,n:rolling(s,n).min(),Sum=lambda s,n:rolling(s,n).sum(),
        Quantile=lambda s,n,q:rolling(s,n).quantile(q),
        Rank=lambda s,n:rolling(s,n).rank(pct=True),
        IdxMax=lambda s,n:rolling(s,n).apply(lambda a:a.argmax()+1,raw=True),
        IdxMin=lambda s,n:rolling(s,n).apply(lambda a:a.argmin()+1,raw=True),
        Slope=lambda s,n:regression(s,n,"slope"),Rsquare=lambda s,n:regression(s,n,"rsquare"),
        Resi=lambda s,n:regression(s,n,"resi"),Corr=lambda a,b,n:rolling(a,n).corr(b),
        Greater=np.maximum,Less=np.minimum,Abs=np.abs,Log=np.log,**daily.to_dict("series"))
    return eval(formula.replace("$",""),{"__builtins__":{}},env).to_numpy()


def evaluate(out):
    daily = fixture()
    loader = ROOT/"references/qlib/qlib/contrib/data/loader.py"
    ref = source(loader,{"QlibDataLoader":object},{"Alpha158DL"})
    expressions,names = ref["Alpha158DL"].get_feature_config()
    ops.Feature._load_internal = lambda self,_instrument,first,last,_freq:daily[self._name].iloc[first:last+1]
    built = {cls.__name__:cls for cls in ops.OpsList}
    rows, oracle = [],{}
    for expression,name in zip(expressions,names):
        code = re.sub(r"\$([a-z]+)",r'Feature("\1")',expression)
        installed = eval(code,{"__builtins__":{}},built).load("DAILY_FIXTURE",0,len(daily)-1,"day").to_numpy()
        own = independent(expression,daily)
        use = slice(61,None)
        n = int((np.isfinite(own[use]) & np.isfinite(installed[use])).sum())
        assert n==len(daily)-61, name
        err = float(np.nanmax(np.abs(own[use]-installed[use])))
        np.testing.assert_allclose(own[use],installed[use],atol=1e-6,rtol=2e-5,err_msg=name)
        name_key = name.replace("_","").lower()
        oracle[name_key] = own
        rows.append(dict(project="Qlib Alpha158",factor=name,formula=expression,status="NUMERICALLY_REPRODUCED",
                         scope="fully warmed daily fixture, all algorithms; compiled upstream 0.9.7",
                         comparisons=n, maximum_abs_delta=err,engine=qlib_version,
                         source_commit="54355232463878d2eebb91fe0ee5fa7fa1f5976c",
                         implementation="factor_research/upstream_daily.py"))
    rows.extend(veighna(daily,oracle))
    out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(out/"upstream_daily_parity.csv",index=False)
    print(pd.DataFrame(rows).groupby(["project","status"]).size().to_string())


def veighna(daily, oracle):
    root = ROOT/"references/vnpy/vnpy/alpha/dataset"
    class FormulaCapture:
        def __init__(self, **_):
            self.expressions = {}
        def add_feature(self,name,expression):
            self.expressions[name] = expression
        def set_label(self,label):
            self.label = label
    scope = dict(pl=pl,np=np,stats=stats,sliding_window_view=sliding_window_view,Callable=Callable,
                 datetime=datetime,Enum=Enum,Real=Real,Union=Union,cast=cast)
    source(root/"utility.py",scope,{"DataProxy"})
    source(root/"ts_function.py",scope,imports=True)
    env = source(root/"datasets/alpha_158.py",{"AlphaDataset":FormulaCapture,"pl":pl},{"Alpha158"})
    formulas = env["Alpha158"](None,None,None,None).expressions
    ref = pl.DataFrame({"datetime":pd.date_range("2024-01-01",periods=len(daily)).to_list(),
                        "vt_symbol":["DAILY_FIXTURE"]*len(daily),**daily.to_dict("list")})
    for col in daily:
        scope[col] = scope["DataProxy"](ref.select("datetime","vt_symbol",col))
    result = []
    for name, formula in formulas.items():
        a = eval(formula,{"__builtins__":{}},scope).df.sort("datetime")["data"].to_numpy()[61:]
        b = oracle[name.replace("_","")][61:]
        n = int((np.isfinite(a)&np.isfinite(b)).sum())
        err = float(np.max(np.abs(a-b))) if n==len(a) else np.nan
        matches = n==len(a) and np.allclose(a,b,atol=1e-6,rtol=2e-5)
        result.append(dict(project="VeighNa Alpha158",factor=name,formula=formula,
                status="NUMERICALLY_REPRODUCED" if matches else "FORMULA_REVIEWED",
                scope="complete-window agreement with independent daily oracle" if matches else
                      "source executed; upstream VeighNa and Qlib oracle differ in this fixture",
                comparisons=n,maximum_abs_delta=err,engine=f"Polars {pl.__version__}",
                source_commit="c6e231caf32b7fc97e6459817fff66458cf7e7c4",
                implementation="factor_research/upstream_daily.py"))
    return result


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    evaluate(parser.parse_args().output)
