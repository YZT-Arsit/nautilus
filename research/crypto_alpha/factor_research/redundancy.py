"""Compare factor levels, rank levels and the factor IC streams."""
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from training.metrics import section_ic


def correlation(view, names, ic, minimum):
    x = view["x"].transpose(0,2,1)
    n = len(names)
    pearson, rank = np.eye(n), np.eye(n)
    for j in range(n-1):
        a,b = x[:,j:j+1,:],x[:,j+1:,:]
        for ranked, array in ((False,pearson),(True,rank)):
            values,_ = section_ic(a,b,minimum,ranked)
            col = pd.DataFrame(values).mean().to_numpy()
            array[j,j+1:] = array[j+1:,j] = col
    return {"pearson":pd.DataFrame(pearson,index=names,columns=names),
            "spearman":pd.DataFrame(rank,index=names,columns=names),
            "ic_series":pd.DataFrame(ic,columns=names).corr(min_periods=24)}


def select(correlations, stats, cfg):
    distance = np.clip(1-np.nan_to_num(correlations["spearman"].abs().to_numpy()),0,1)
    np.fill_diagonal(distance,0)
    groups = fcluster(linkage(squareform(distance,checks=False),method="complete"),
                      t=1-cfg["cluster_abs_spearman"],criterion="distance") if len(distance)>1 else [1]
    cluster = stats.assign(cluster=groups).sort_values(["cluster","train_only_score","factor"],ascending=[True,False,True])
    cluster["cluster_size"] = cluster.groupby("cluster").factor.transform("size")
    cluster["representative"] = ~cluster.cluster.duplicated()
    cluster["representative_name"] = cluster.groupby("cluster").factor.transform("first")
    representative = cluster.loc[cluster.representative,"factor"].tolist()
    stable = cluster.loc[cluster.stable].sort_values(["train_only_score","factor"],ascending=[False,True])
    stable = stable.drop_duplicates("cluster").head(cfg["stable_max_factors"]).factor.tolist()
    return cluster, representative, stable


def stability(table, names, cfg):
    h = table.loc[table.holding_minutes.eq(cfg["main_horizon"])].set_index(["split","factor"])
    train = h.loc["TRAIN"].reindex(names)
    val = h.loc["INTERNAL_VALIDATION"].reindex(names)
    frame = pd.DataFrame(dict(factor=names, train_ic=train.mean_ic, train_direction=train.train_direction,
                              directed_train_ic=train.directed_ic,validation_ic=val.mean_ic,
                              directed_validation_ic=val.directed_ic,train_missing_rate=train.missing_rate,
                              train_positive_month_fraction=train.positive_month_fraction,
                              validation_positive_month_fraction=val.positive_month_fraction)).reset_index(drop=True)
    frame["train_only_score"] = frame.directed_train_ic*frame.train_positive_month_fraction
    frame["stable"] = ((frame.directed_train_ic>0)&(frame.directed_validation_ic>0)
                       &(frame.train_positive_month_fraction>=cfg["stable_positive_month_fraction"])
                       &(frame.validation_positive_month_fraction>=cfg["stable_positive_month_fraction"])
                       &(frame.train_missing_rate<=1-cfg["minimum_factor_coverage"]))
    return frame
