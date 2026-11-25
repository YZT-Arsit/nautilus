import numpy as np
from data_engine.events import FundingRateEvent
from test_backtest import example
from backtest.replay import replay

def test_actual_mark_pre_rebalance_once():
    time,px,targets = example()
    event = FundingRateEvent(event_time_ns=time[1].value, instrument_id="A", funding_rate=.001, mark_price=103.)
    result = replay(px,targets,{"A":[event,event]},10000.,0,0,0)
    assert np.isclose(result.funding.sum(), -5.15)
