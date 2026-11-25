import numpy as np
from test_backtest import example
from backtest.replay import replay

def test_rebalances_only_delta():
    _, px, targets = example()
    result = replay(px, targets, {}, 10000.,0,0,0)
    assert np.isclose(result.iloc[0].turnover_usdt, 10000)
    assert result.iloc[1].turnover_usdt < 600
