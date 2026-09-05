import pandas as pd

from scripts.internal.build_stagea_9symbol_expanded_tick_review import add_selection


def test_timeframe_specific_strict_selection() -> None:
    frame = pd.DataFrame(
        {
            "strategy_id": ["a"] * 8,
            "timeframe": ["1m", "1m", "1m", "10m", "10m", "10m", "15m", "15m"],
            "Sharpe": [-1.6, 1.5, 2.0, -1.1, 1.1, 1.0, 1.2, -1.2],
            "Signed_BE_bps": [0.0, 99.0, 0.0, -11.0, 10.0, 20.0, 11.0, -11.0],
        }
    )
    result = add_selection(frame)
    assert result.QUALIFY_1M.tolist() == [True, False, True, False, False, False, False, False]
    assert result.QUALIFY_10M15M.tolist() == [False, False, False, True, False, False, True, True]
    assert result.CASE_QUALIFIES.tolist() == [True, False, True, True, False, False, True, True]
    assert result.POSITIVE_SHARPE_1M.tolist() == [False, False, True, False, False, False, False, False]
    assert result.POSITIVE_BE_SHARPE.tolist() == [False, False, False, False, False, False, True, False]


def test_selection_is_strategy_level_or() -> None:
    frame = pd.DataFrame(
        {
            "strategy_id": ["selected", "selected", "not_selected"],
            "timeframe": ["1m", "15m", "10m"],
            "Sharpe": [1.6, 0.0, 2.0],
            "Signed_BE_bps": [0.0, 0.0, 9.0],
        }
    )
    result = add_selection(frame)
    assert result.STRATEGY_SELECTED.tolist() == [True, True, False]
