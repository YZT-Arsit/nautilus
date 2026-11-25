import pandas as pd
from labels.alignment import align, bar_available_index

def test_clock_and_missing_across_midnight():
    when = pd.DatetimeIndex(["2025-01-01 09:00", "2025-01-01 23:00"], tz="UTC")
    opens = pd.DatetimeIndex(["2025-01-01 09:09", "2025-01-01 10:09", "2025-01-01 23:09"], tz="UTC")
    prices = pd.Series([100., 105., 90.], index=bar_available_index(opens))
    result = align(when, prices, 10, 60)
    assert abs(result.label_raw.iloc[0] - .05) < 1e-12
    assert result.entry_time.iloc[0] == pd.Timestamp("2025-01-01 09:10Z")
    assert result.label_available_time.iloc[0] == pd.Timestamp("2025-01-01 10:10Z")
    assert result.label_end_time.iloc[1] == pd.Timestamp("2025-01-02 00:10Z")
    assert pd.isna(result.label_raw.iloc[1])

def test_bar_open_is_never_the_close_availability():
    ts = pd.DatetimeIndex(["2025-01-01 08:59", "2025-01-01 09:00"], tz="UTC")
    assert bar_available_index(ts)[0] == ts[1]
