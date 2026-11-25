"""Numerical comparisons with pinned upstream function bodies."""
import ast
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import polars as pl

from foundation.evaluation import quantile_bins, quantiles, relative_strength
from training.reproduce import environment, load_functions, parity
from v3.common import ROOT, digest, write_json


def verify(out):
    root = ROOT/"references"
    parity(out)
    evidence = json.loads((out/"numeric_parity.json").read_text())
    source = root/"vnpy/vnpy/alpha/dataset"
    proxy = SimpleNamespace
    cs = load_functions(source/"cs_function.py", ["cs_rank"], {"pl":pl,"DataProxy":proxy_from_frame})
    ts = load_functions(source/"ts_function.py", ["ts_delay"], {"pl":pl,"DataProxy":proxy_from_frame})
    index = pd.MultiIndex.from_product([pd.date_range("2024-01-01", periods=3, tz="UTC"), list("ABC")],
                                       names=["prediction_time","symbol"])
    fixture = pd.Series([1., 1., 2., 3., 1., 2., 1., np.nan, 3.], index=index)
    inputs = pl.DataFrame(dict(datetime=index.get_level_values(0).to_list(),
                               vt_symbol=index.get_level_values(1).to_list(), data=fixture.to_list()))
    own_rank = fixture.groupby(level=0).rank(method="min", pct=True)
    converted = proxy(df=inputs)
    np.testing.assert_allclose(cs["cs_rank"](converted).df["data"].to_numpy(), own_rank.to_numpy())
    expect = fixture.groupby(level=1).shift(1)
    np.testing.assert_allclose(ts["ts_delay"](converted, 1).df["data"].to_numpy(), expect.to_numpy())
    assert relative_strength(fixture, "rank").iloc[0] == 1.5

    freq = root/"freqtrade/freqtrade/freqai/data_kitchen.py"
    parsed = ast.parse(freq.read_text(encoding="utf-8"))
    owner = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "FreqaiDataKitchen")
    nodes = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in
             {"slice_dataframe", "set_new_model_names"}]
    scope = dict(TimeRange=object, DataFrame=pd.DataFrame, Path=Path)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(freq), "exec"), scope)
    time = datetime(2025, 7, 1, tzinfo=UTC)
    frame = pd.DataFrame({"date":[time, time+timedelta(days=1), time+timedelta(days=2)]})
    params = SimpleNamespace(startdt=time, stopdt=time+timedelta(days=2))
    state = SimpleNamespace(live=False, full_path=out)
    pd.testing.assert_frame_equal(scope["slice_dataframe"](state, params, frame), frame.iloc[:2])
    scope["set_new_model_names"](state, "BTC/USDT", 123)
    assert state.model_filename == "cb_btc_123" and state.data_path.name == "sub-train-BTC_123"

    x = np.array([[1.,2.,3.,4.,5.,6.],[6.,1.,2.,3.,4.,5.],[5.,6.,1.,2.,3.,4.]])
    y = x/100
    dates = pd.date_range("2024-01-01", periods=len(x))
    keys = pd.MultiIndex.from_product([dates,list("ABCDEF")],names=["date","asset"])
    al_path = root/"alphalens/alphalens/performance.py"
    util_path = root/"alphalens/alphalens/utils.py"
    utils = load_functions(util_path, ["get_forward_returns_columns","demean_forward_returns"], environment())
    env = {**environment(),"utils":SimpleNamespace(**utils)}
    perf = load_functions(al_path, ["mean_return_by_quantile","quantile_turnover"], env)
    cuts = np.stack([pd.qcut(row,3,labels=False)+1 for row in x])
    np.testing.assert_array_equal(quantile_bins(x), cuts)
    factor = pd.DataFrame({"factor":x.ravel(),"1h":y.ravel(),"factor_quantile":cuts.ravel()},index=keys)
    means, _ = perf["mean_return_by_quantile"](factor, by_date=True)
    returns, turns = quantiles(x,y)
    for q in returns:
        np.testing.assert_allclose(returns[q], means["1h"].loc[q].reindex(dates))
        np.testing.assert_allclose(turns[q], perf["quantile_turnover"](factor.factor_quantile, q).reindex(dates))

    qlib_path = root/"qlib/qlib/contrib/data/loader.py"
    qlib = load_functions(qlib_path, ["Alpha158DL"], {"QlibDataLoader":object})
    formulas, names = qlib["Alpha158DL"].get_feature_config()
    original = []
    for name, expression in zip(names, formulas):
        checked = name.startswith(("ROC","MA","STD","MAX","MIN")) or name in {
            "KMID","KLEN","KMID2","KUP","KUP2","KLOW","KLOW2","KSFT","KSFT2","OPEN0","HIGH0","LOW0","VWAP0"}
        original.append(dict(factor_name=name, upstream_expression=expression,
                             status="DAILY_NUMERIC_PARITY" if checked else "EXPRESSION_INVENTORY_ONLY"))
    daily = pd.DataFrame(original)
    assert daily.status.eq("DAILY_NUMERIC_PARITY").sum() == evidence["qlib_alpha158_daily_core_numeric_count"]
    daily.to_csv(out/"alpha158_original_formula_inventory.csv", index=False)
    evidence.update(veighna_rank_min_percentile=True, veighna_grouped_delay=True, freqai_half_open_slice=True,
                    freqai_frozen_version_naming_unit=True, alphalens_foundation_terciles=True,
                    source_sha256={str(p.relative_to(root)):digest(p) for p in
                    (qlib_path, al_path, util_path, source/"cs_function.py", source/"ts_function.py", freq)})
    write_json(out/"numeric_parity.json", evidence)
    history = pd.read_csv(root/"reproduction.csv").query("repository in ['qlib','alphalens','freqtrade']")
    history["run_scope"] = "archived V1 evidence"
    fresh = [
        ("qlib","microsoft/qlib","54355232463878d2eebb91fe0ee5fa7fa1f5976c","MIT",
         "qlib/contrib/{data/loader,eva/alpha}.py", "158 expressions catalogued; 38 daily expressions and IC/normalizers tested",
         "88 minute crypto factors are independently defined; no Alpha158 parity claim"),
        ("alphalens","quantopian/alphalens","77084f1e4c2c0be407e032d444fb19e4be4b0f37","Apache-2.0",
         "alphalens/{performance,utils}.py", "original Rank IC, weights, returns, terciles and set turnover",
         "per-clock metrics; bins depend on eligible signals; observations without realized prices excluded from returns"),
        ("VeighNa","vnpy/vnpy","c6e231caf32b7fc97e6459817fff66458cf7e7c4","MIT",
         "vnpy/alpha/dataset/{cs_function,ts_function}.py", "CS min percentile rank and grouped time-series delay",
         "foundation uses average rank, finite masks and completed minutes"),
        ("FreqAI","freqtrade/freqtrade","7fcb509a434cfaa5e0943a7ccc005967cfd0b9d9","GPL-3.0",
         "freqtrade/freqai/data_kitchen.py", "offline half-open slicing and upstream naming unit",
         "annual freeze for shared cross-sectional model with explicit label maturity"),
    ]
    columns = ["repository","url","commit_sha","license","relevant_source_files",
               "original_implementation","adapted_implementation"]
    current = pd.DataFrame(fresh, columns=columns)
    current["url"] = "https://github.com/" + current.url
    current = current.assign(status="UNIT_REPRODUCED", result="numeric_parity.json", run_scope="foundation rerun")
    pd.concat([history,current],ignore_index=True).to_csv(out/"reproduction.csv", index=False)
    return evidence


def proxy_from_frame(frame):
    return SimpleNamespace(df=frame)
