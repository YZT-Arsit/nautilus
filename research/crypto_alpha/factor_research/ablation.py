"""Fixed-budget factor groups using Foundation model and replay contracts."""
from time import perf_counter

import numpy as np
import pandas as pd

from foundation.annual import subset, train_family
from foundation.model import Model, OBJECTIVES
from foundation.portfolio import replay

GROUPS = ["Momentum","Reversal","Volatility","Volume and Order Flow","Cross-sectional","Derivatives"]
VOLUME = {"Volume","Liquidity","Order Flow"}


def sets(reg, representative, stable):
    all_names = reg.factor_name.tolist()
    results = {"full":all_names}
    for group in GROUPS:
        mask = reg.category.isin(VOLUME) if group=="Volume and Order Flow" else reg.category.eq(group)
        results[f"only_{group}"] = reg.loc[mask,"factor_name"].tolist()
        results[f"without_{group}"] = reg.loc[~mask,"factor_name"].tolist()
    return {**results, "deduplicated":representative, "stable":stable}


def ablate(panel, window, models, candidates, cfg, out):
    fit = panel.view(window.train_start,window.validation_start)
    val = panel.view(window.validation_start,window.test_start)
    rows, curves = [],[]
    for name, features in candidates.items():
        if not features:
            continue
        cols = [panel.factors.index(f) for f in features]
        f,v = subset(fit,cols),subset(val,cols)
        for family, params in models.items():
            start = perf_counter()
            model = Model(family,params,cfg,features).fit(f,v)
            stats,_ = model.evaluate(model.predict(v),v)
            rows.append(dict(subset=name,model=family,features=len(features),target=cfg["target"],
                             objective=OBJECTIVES[family],selected_iterations=model.rounds,seconds=perf_counter()-start,
                             split="INTERNAL_VALIDATION",**stats))
            curves.extend(dict(subset=name,model=family,step=i,loss=l,validation_ic=ic)
                          for i,l,ic in model.history)
        print(f"ABLATION {name} {len(features)}",flush=True)
    scores = pd.DataFrame(rows)
    baseline = scores.loc[scores.subset.eq("full")].set_index("model")
    scores["incremental_ic"] = scores.ic-scores.model.map(baseline.ic)
    scores["incremental_rank_ic"] = scores.rank_ic-scores.model.map(baseline.rank_ic)
    scores.to_csv(out/"factor_ablation.csv",index=False)
    pd.DataFrame(curves).to_csv(out/"ablation_training_log.csv",index=False)
    means = scores.groupby("subset").agg(mean_ic=("ic","mean"),mean_rank_ic=("rank_ic","mean"),
                                        incremental_ic=("incremental_ic","mean"),minimum_ic=("ic","min"),
                                        features=("features","first")).sort_values("mean_ic",ascending=False)
    means.to_csv(out/"ablation_cross_model.csv")
    return scores, means


def historical(panel, window, model_params, candidates, chosen, cfg, funding, proxies, out):
    stats, ledgers = [],[]
    for name in dict.fromkeys(["full",chosen]):
        features = candidates[name]
        for family, params in model_params.items():
            _, rec, _, _, view, signal = train_family(family,params,cfg,panel,window,features,
                                                       out/"frozen_models"/name/family)
            sem, book, _, export = replay(signal,panel,view,cfg,funding,proxies,f"{name}-{family}")
            stats.append(dict(subset=name, model=family, split="OBSERVED_HISTORICAL_TEST",
                              ic=rec["ic"],rank_ic=rec["rank_ic"],icir=rec["icir"],**sem))
            export.to_parquet(out/"frozen_models"/name/family/"nautilus_targets.parquet",index=False)
            ledgers.append(book.reset_index(drop=True).assign(subset=name,model=family))
    pd.DataFrame(stats).to_csv(out/"observed_historical_portfolio.csv",index=False)
    pd.concat(ledgers).to_parquet(out/"observed_historical_ledger.parquet",index=False,compression="zstd")
    return stats
