import numpy as np
import pandas as pd
from factors.library import compute

def test_future_bars_do_not_change_past():
    idx = pd.date_range("2025-01-01", periods=2500, freq="min", tz="UTC")
    x = np.arange(len(idx), dtype=float)
    bar = pd.DataFrame({"open":100+x/10, "close":100.1+x/10, "high":101+x/10,
                        "low":99+x/10, "volume":10+x%3, "quote_volume":1000+x,
                        "trade_count":11+x%7,"taker_buy_volume":3+x%2,
                        "taker_buy_quote_volume":310+x/10}, index=idx)
    cut = idx[2000]
    full, registry = compute(bar, pd.DatetimeIndex([cut]))
    prefix, _ = compute(bar.loc[:cut], pd.DatetimeIndex([cut]))
    pd.testing.assert_frame_equal(full, prefix)
    assert len(registry) >= 45
