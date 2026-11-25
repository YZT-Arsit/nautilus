"""Shared per-timestamp evaluation and relative-strength operators."""
import warnings

import numpy as np
import pandas as pd

from portfolio.weights import weights
from training.metrics import section_ic, score_summary
from training.protocol import factor_diagnostics


def relative_strength(values, operation, minimum=3, epsilon=1e-12):
    values = values.where(np.isfinite(values))
    by_time = values.groupby(level="prediction_time")
    if operation == "raw":
        return values
    if operation in {"rank", "rank_zscore"}:
        values = by_time.rank(method="average")
        by_time = values.groupby(level="prediction_time")
    if operation == "rank":
        return values
    enough = by_time.transform("count") >= minimum
    if operation == "l1":
        magnitude = values.abs().groupby(level="prediction_time").transform("sum")
        return values.div(magnitude.where(enough & (magnitude > epsilon)))
    centered = (values - by_time.transform("mean")).where(enough)
    if operation == "demean":
        return centered
    if operation in {"zscore", "rank_zscore"}:
        std = by_time.transform("std", ddof=0)
        return centered.div(std.where(std > epsilon))
    raise ValueError(f"unknown relative-strength operation {operation}")


def quantile_bins(x, count=3):
    """Linear quantile edges as in qcut. Reject a section with indistinct edges."""
    x = np.where(np.isfinite(x), x, np.nan)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered")
        edges = np.nanquantile(x, np.linspace(0., 1., count+1), axis=1).T
    good = (np.diff(edges, axis=1) > 0).all(axis=1) & (np.isfinite(x).sum(axis=1) >= count)
    bins = 1 + (x[:, :, None] > edges[:, None, 1:-1]).sum(axis=-1)
    return np.where(good[:, None] & np.isfinite(x), bins, 0)


def quantiles(x, y, count=3):
    # Portfolio membership depends on the known scores. Evaluation removes
    # unavailable outcomes and demeans the return of the evaluated population.
    bins = quantile_bins(x, count)
    paired = np.isfinite(y) & (bins > 0)
    total = paired.sum(axis=1)
    mean = np.where(paired, y, 0).sum(axis=1) / np.maximum(total, 1)
    returns, turns = {}, {}
    for q in range(1, count+1):
        members = bins == q
        valid = members & paired
        n = valid.sum(axis=1)
        returns[q] = np.divide(np.where(valid, y-mean[:, None], 0).sum(axis=1), n,
                               out=np.full(len(y), np.nan), where=n > 0)
        present = np.flatnonzero(members.any(axis=1))
        turnover = np.full(len(y), np.nan)
        curr, prev = present[1:], present[:-1]
        turnover[curr] = (members[curr] & ~members[prev]).sum(axis=1) / members[curr].sum(axis=1)
        turns[q] = turnover
    return returns, turns


def evaluate(scores, view, minimum, label):
    raw = view["raw"]
    raw_ic, n = section_ic(scores, raw, minimum)
    rank_ic, _ = section_ic(scores, raw, minimum, True)
    target_ic, _ = section_ic(scores, view["labels"][label], minimum)
    p, r, t = (score_summary(a) for a in (raw_ic, rank_ic, target_ic))
    series = pd.DataFrame({"prediction_time": view["times"], "ic": raw_ic, "rank_ic": rank_ic,
                           "processed_ic": target_ic, "valid_assets": n})
    return dict(ic=p["mean"], rank_ic=r["mean"], icir=p["ir"], processed_ic=t["mean"],
                valid_sections=p["valid_sections"], skipped_sections=p["skipped_sections"],
                valid_pairs=int(n.sum())), series


def factor_report(dataset_by_horizon, names, start, end, cfg, out):
    minimum = cfg["min_assets"]
    chosen_horizon = cfg["holding_minutes"]
    _, _, corr, selected = factor_diagnostics(dataset_by_horizon(chosen_horizon).view(start, end), names, minimum)
    corr.to_csv(out/"factor_correlation.csv")
    results, monthly = [], []
    for h in sorted(set(cfg["evaluation_horizons_minutes"]) | {chosen_horizon}):
        view = dataset_by_horizon(h).view(start, end)
        eligible = view["eligible"]
        for i, name in enumerate(names):
            x = np.where(eligible, view["x"][:, :, i], np.nan)
            p, n = section_ic(x, view["raw"], minimum)
            r, _ = section_ic(x, view["raw"], minimum, True)
            stats = score_summary(p)
            m = pd.DataFrame({"ic": p, "rank_ic": r}).groupby(view["times"].strftime("%Y-%m")).agg(
                ic=("ic", "mean"), rank_ic=("rank_ic", "mean"), valid_sections=("ic", "count"))
            monthly.append(m.rename_axis("month").reset_index().assign(factor_name=name, holding_minutes=h))
            row = dict(factor_name=name, holding_minutes=h, mean_ic=stats["mean"],
                       mean_rank_ic=score_summary(r)["mean"], icir=stats["ir"],
                       valid_sections=stats["valid_sections"], skipped_sections=stats["skipped_sections"],
                       coverage=np.isfinite(x).sum()/max(1, eligible.sum()), mean_assets=n.mean(),
                       positive_ic_ratio=stats["positive_ratio"])
            if h == chosen_horizon:
                q, t = quantiles(x, view["raw"])
                for k in q:
                    row[f"quantile_{k}_return"] = score_summary(q[k])["mean"]
                    row[f"quantile_{k}_turnover"] = score_summary(t[k])["mean"]
            results.append(row)
    pd.DataFrame(results).to_csv(out/"factor_evaluation.csv", index=False)
    pd.concat(monthly, ignore_index=True).to_csv(out/"monthly_factor_ic.csv", index=False)
    return selected


def target_weights(scores, cfg, method):
    return tuple(map(np.stack, zip(*(weights(row, method, cfg["gross"], cfg["max_weight"],
                                               cfg["min_assets"]) for row in scores))))
