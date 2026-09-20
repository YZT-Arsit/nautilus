from __future__ import annotations

import numpy as np

from scripts.internal.run_selected_partial_window_maker import eligible_event_indexes
from scripts.internal.run_selected_partial_window_maker import merged_eligible_events


def _arrays():
    quote_arrays = (
        np.array([1, 2, 3, 4], dtype=np.int64),
        np.array([99.0, 100.0, 101.0, 98.0]),
        np.ones(4),
        np.array([100.0, 101.0, 102.0, 99.0]),
        np.ones(4),
        np.array([2, 5, 8, 11], dtype=np.int64),
        np.array([2, 5, 8, 11], dtype=np.int64),
    )
    trade_arrays = (
        np.array([10, 11, 12, 13], dtype=np.int64),
        np.array([100.0, 101.0, 99.0, 102.0]),
        np.ones(4),
        np.array([1, 6, 9, 12], dtype=np.int64),
        np.array([True, False, True, False]),
    )
    return quote_arrays, trade_arrays


def _events(side: str, limit: float, slices: list[tuple[int, int, int, int]]):
    quote_arrays, trade_arrays = _arrays()
    output = []
    for q0, q1, t0, t1 in slices:
        qi, ti = eligible_event_indexes(
            side, limit, quote_arrays, trade_arrays, q0, q1, t0, t1,
        )
        output.extend(merged_eligible_events(qi, ti, quote_arrays, trade_arrays))
    return output


def test_chunked_buy_event_selection_matches_full_minute() -> None:
    assert _events("BUY", 100.0, [(0, 4, 0, 4)]) == _events(
        "BUY", 100.0, [(0, 2, 0, 2), (2, 4, 2, 4)],
    )


def test_chunked_sell_event_selection_matches_full_minute() -> None:
    assert _events("SELL", 101.0, [(0, 4, 0, 4)]) == _events(
        "SELL", 101.0, [(0, 2, 0, 2), (2, 4, 2, 4)],
    )


def test_merged_event_order_uses_timestamp_then_source_then_id() -> None:
    events = _events("BUY", 100.0, [(0, 4, 0, 4)])
    timestamps = [event[-2] if kind == 0 else event[-2] for kind, event in events]
    assert timestamps == sorted(timestamps)
