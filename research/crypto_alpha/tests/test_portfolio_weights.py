import numpy as np
from portfolio.weights import weights

def test_rank_l1_neutral_cap_and_missing():
    score = np.array([0,1,1,2,3,4,5,np.nan])
    z, w = weights(score, "rank", 1., .25, 3)
    assert np.isclose(w.sum(), 0)
    assert np.isclose(abs(w).sum(), 1)
    assert abs(w).max() <= .25 + 1e-12
    assert w[-1] == 0 and np.isnan(z[-1])
    assert np.all(weights(np.ones(9), "rank", 1., .25, 3)[1] == 0)

def test_export_has_a_single_signal_and_no_outcome_columns():
    import pandas as pd
    from portfolio.interface import validate_export
    time = pd.Timestamp("2025-01-01 09:00Z")
    rows=pd.DataFrame({"prediction_time":[time]*3,"execution_time":[time+pd.Timedelta(minutes=10)]*3,
                      "model_version":["v1"]*3,"symbol":["A","B","C"],"prediction_score":[0,1,np.nan],
                      "relative_strength":[-1,1,np.nan],"target_weight":[-.5,.5,0.],"label_raw":[0.,-2.,3.]})
    assert list(validate_export(rows,10))[-1]=="target_weight"

def test_float32_scores_preserve_float64_constraints():
    scores=np.array([-.89,-.56,-.34,-.21,.09,.11,.33,.53,.99],dtype=np.float32)
    for method in ("rank","raw","equal"):
        _,target=weights(scores,method,1.,.30,3)
        np.testing.assert_allclose(np.abs(target).sum(),1.,atol=1e-12,rtol=0)
        np.testing.assert_allclose(target.sum(),0.,atol=1e-12,rtol=0)
