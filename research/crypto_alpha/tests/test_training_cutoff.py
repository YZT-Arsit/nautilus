import pandas as pd
from labels.alignment import mature

def test_label_and_prediction_boundaries():
    time = pd.Timestamp("2025-01-01 09:00Z")
    frame = pd.DataFrame({"prediction_time": [time, time + pd.Timedelta(hours=1)],
                          "label_available_time": [time + pd.Timedelta(minutes=70), pd.NaT],
                          "label_raw": [1., 2.]})
    assert mature(frame, time, time + pd.Timedelta(hours=1)).empty
    assert len(mature(frame, time, time + pd.Timedelta(minutes=70))) == 1
