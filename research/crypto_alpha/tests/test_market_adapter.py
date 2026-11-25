import pandas as pd
from data.market import read_bars, symbol_dir

def test_read_primary_and_only_missing_supplemental_partition(tmp_path):
    primary,extra=tmp_path/"market",tmp_path/"extra"
    t=pd.Timestamp("2024-07-01",tz="UTC")
    for root, date, close in [(primary,"2024-07-01",101.),(extra,"2024-07-01",999.),
                              (extra,"2024-06-30",98.)]:
        path=symbol_dir(root,"BTCUSDT")/f"data_type=bar/freq=1m/date={date}"
        path.mkdir(parents=True)
        pd.DataFrame({"ts":[pd.Timestamp(date)],"open":[close],"high":[close],"low":[close],
                      "close":[close],"volume":[1.],"source":["binance_vision"]}).to_parquet(path/"part-0.parquet")
    frame,audit,inventory=read_bars(primary,"BTCUSDT",t-pd.Timedelta(days=1),t+pd.Timedelta(days=1),t,extra)
    assert audit["supplemental_partitions_read"]==1
    assert len(inventory)==2
    assert frame.close.loc[t+pd.Timedelta(minutes=1)]==101.
    assert pd.isna(frame.close.loc[t+pd.Timedelta(minutes=2)])
