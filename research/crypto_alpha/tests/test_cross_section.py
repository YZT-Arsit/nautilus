import numpy as np
import pandas as pd
from labels.alignment import normalize

def test_ties_missing_constant_and_small():
    data = pd.DataFrame({"prediction_time": [0]*4+[1]*3+[2]*2,
                         "label_raw": [1.,1.,4.,np.nan, 2.,2.,2., 0.,1.]})
    out = normalize(data, 3, 1e-12)
    np.testing.assert_allclose(out.label_rank.iloc[:3], [1.5,1.5,3.])
    assert np.isclose(out.label_rank_zscore.iloc[:3].std(ddof=0), 1.)
    assert out.label_rank_zscore.iloc[3:].isna().all()
