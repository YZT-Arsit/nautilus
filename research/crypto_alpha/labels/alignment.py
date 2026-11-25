"""UTC event clocks and maturity-safe supervision."""
import numpy as np
import pandas as pd


def bar_available_index(open_times):
    return open_times + pd.Timedelta(minutes=1)


def align(prediction, close_available, delay, holding):
    entry = prediction + pd.Timedelta(minutes=delay)
    end = entry + pd.Timedelta(minutes=holding)
    p0 = close_available.reindex(entry).to_numpy()
    p1 = close_available.reindex(end).to_numpy()
    result = pd.DataFrame({
        "prediction_time": prediction, "entry_time": entry, "label_end_time": end,
        "label_available_time": end, "label_entry_price": p0, "label_exit_price": p1,
        "label_raw": p1 / p0 - 1,
    }, index=prediction)
    return result


def mature(frame, start, cutoff):
    return frame.loc[(frame.prediction_time >= start) & (frame.prediction_time < cutoff)
                     & (frame.label_available_time <= cutoff) & frame.label_raw.notna()]


def normalize(frame, min_assets, epsilon):
    result = frame.copy()
    raw_group = result.groupby("prediction_time")["label_raw"]
    result["label_rank"] = raw_group.rank(method="average")
    enough = raw_group.transform("count") >= min_assets
    for col, dest in (("label_raw", "label_raw_zscore"), ("label_rank", "label_rank_zscore")):
        values = result[col]
        section = values.groupby(result.prediction_time)
        std = section.transform("std", ddof=0)
        result[dest] = (values - section.transform("mean")).div(std.where(enough & (std > epsilon)))
    return result
