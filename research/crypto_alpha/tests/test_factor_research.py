import numpy as np
import pandas as pd

from factor_research.statistics import block_interval, paired_ic, decay
from factor_research.redundancy import select


def test_time_blocks_are_deterministic_and_keep_invalid_series_missing():
    times = pd.date_range("2024-01-01", periods=21,freq="D",tz="UTC")
    x = np.column_stack([np.repeat([.15,-.1,.2],7),np.repeat(np.nan,21)])
    a = block_interval(times,x,7,500,42)
    np.testing.assert_array_equal(a,block_interval(times,x,7,500,42))
    assert a[0,0] < x[:,0].mean() < a[0,1]
    assert np.isnan(a[1]).all()


def test_ic_matches_pairwise_asset_masks_and_constant_is_missing():
    x = np.array([[[1.,2.],[2.,2.],[3.,2.],[4.,2.]],
                  [[1.,1.],[2.,2.],[np.nan,3.],[4.,4.]]])
    label = np.array([[1.,2.,3.,4.],[1.,2.,3.,np.nan]])
    p,n,r=paired_ic(x,label,3)
    np.testing.assert_array_equal(n,[[4,4],[2,3]])
    assert np.isnan(p[0,1]) and np.isnan(r[1,0])
    assert p[0,0]==1 and r[1,1]==1


def test_complete_linkage_separates_weak_transitive_pairs():
    names=list("ABC")
    spearman=pd.DataFrame([[1.,-.91,.3],[-.91,1.,.92],[.3,.92,1.]],index=names,columns=names)
    frame=pd.DataFrame({"factor":names,"train_only_score":[.01,.02,.03],"stable":[True,True,True]})
    found,reps,stable=select({"spearman":spearman},frame,
                              {"cluster_abs_spearman":.85,"stable_max_factors":8})
    assert found.cluster.nunique()==2 and set(reps)==set(stable)
    assert found.set_index("factor").loc["A","cluster"]!=found.set_index("factor").loc["C","cluster"]


def test_training_horizon_does_not_follow_validation_or_historical_scores():
    table=pd.DataFrame([
        {"factor":"old","holding_minutes":h,"split":s,"directed_ic":i,
         "mean_ic":i,"positive_month_fraction":1.}
        for h,a,b,c in [(60,.03,.02,-.1),(240,.01,.08,.9)]
        for s,i in zip(["TRAIN","INTERNAL_VALIDATION","OBSERVED_HISTORICAL_TEST"],[a,b,c])])
    assert decay(table,{})["best_training_horizon"].tolist()==[60,60]
