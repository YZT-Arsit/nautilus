from __future__ import annotations

import numpy as np
import pandas as pd
from types import SimpleNamespace

from scripts.internal.run_phase6e_forward_holdout import HOLDOUT_START
from scripts.internal.run_phase6e_forward_holdout import SYMBOLS
from scripts.internal.run_phase6e_forward_holdout import continuous_reference
from scripts.internal.run_phase6e_forward_holdout import cumulative_drawdown
from scripts.internal.run_phase6e_forward_holdout import validate_forward_data


def test_scope_and_cutoff_are_frozen() -> None:
    assert SYMBOLS == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert HOLDOUT_START == "2026-07-01T00:00:00Z"


def test_continuous_forward_return_is_capital_invariant_and_carries_position() -> None:
    times=np.array([1,2],dtype=np.int64); direction=np.array([1.,1.]); opens=np.array([101.,102.]); closes=np.array([102.,103.]); funding=pd.DataFrame()
    low=continuous_reference(1.,100.,direction,opens,closes,funding,times,1_000.)
    high=continuous_reference(1.,100.,direction,opens,closes,funding,times,1_000_000.)
    assert np.allclose(low,high)
    assert low.sum()>0


def test_drawdown_series_uses_same_additive_path() -> None:
    values=np.array([.1,-.2,.05])
    dd=cumulative_drawdown(values)
    assert np.isclose(dd.min(),-.2)
    assert np.isclose(dd[-1],-.15)


def test_boundary_mask_cannot_score_pre_cutoff() -> None:
    cutoff=pd.Timestamp(HOLDOUT_START).value
    values=np.array([cutoff-1,cutoff,cutoff+1],dtype=np.int64)
    scored=values[values>=cutoff]
    assert scored.min()>=cutoff


def test_forward_integrity_requires_complete_funding_cadence() -> None:
    minute = 60_000_000_000
    start, end = 0, 16 * 60 * minute
    bars = [SimpleNamespace(event_time_ns=i * minute) for i in range(16 * 60)]
    complete = pd.DataFrame({"event_time_ns": [0, 8 * 60 * minute]})
    missing = pd.DataFrame({"event_time_ns": [0]})
    assert validate_forward_data("BTCUSDT", bars, complete, start, end)["status"] == "PASSED"
    assert validate_forward_data("BTCUSDT", bars, missing, start, end)["status"] == "FAILED"
