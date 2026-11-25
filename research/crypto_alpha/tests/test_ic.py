import numpy as np
from training.metrics import section_ic

def test_ic_pairwise_and_per_time():
    x = np.array([[1,2,3,4],[1,2,3,4],[1,1,1,1]],float)
    y = np.array([[1,2,3,np.nan],[4,3,2,1],[1,2,3,4]],float)
    for rank in (False, True):
        np.testing.assert_allclose(section_ic(x,y,3,rank)[0], [1,-1,np.nan])
