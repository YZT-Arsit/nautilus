import numpy as np
import pandas as pd
from backtest.replay import replay

def example():
    time = pd.date_range("2025-01-01 09:10", periods=3, freq="h", tz="UTC")
    px = pd.DataFrame([[100.,100.],[105.,95.],[106.,94.]], index=time, columns=["A","B"])
    targets = pd.DataFrame([[.5,-.5],[.5,-.5],[0.,0.]], index=time, columns=px.columns)
    return time, px, targets

def test_price_equity_reconciles_and_missing_quotes_reject():
    _, px, targets = example()
    result = replay(px,targets,{},10000.,4,1,1)
    row = result.iloc[-1]
    assert np.isclose(row.equity - 10000, result.eval("gross_pnl + funding - fees - spread - slippage").sum())
    px.iloc[1,0] = np.nan
    result = replay(px,targets,{},10000.,0,0,0)
    assert result.iloc[1].skipped

def test_fixed_notional_simple_series_can_go_below_minus_100_percent():
    _,px,targets=example()
    px.iloc[1:,1] = 400.
    result=replay(px,targets,{},10000.,0,0,0,fixed_capital=True)
    assert result.equity.min()<0
    assert np.isfinite(result.net_return).all()
