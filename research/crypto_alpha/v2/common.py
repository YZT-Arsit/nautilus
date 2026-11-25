"""V2 configuration and clock-aligned data views."""
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
NAUTILUS=ROOT.parents[1]
sys.path[:0]=[str(ROOT),str(NAUTILUS)]

from data.market import symbol_dir


def resolved_config():
    path=ROOT/os.environ.get("CRYPTO_ALPHA_V2_CONFIG","configs/v2.json")
    cfg=json.loads(path.read_text())
    parent=cfg.pop("extends",None)
    return {**json.loads((ROOT/parent).read_text()),**cfg} if parent else cfg


def config_digest():
    return hashlib.sha256(json.dumps(resolved_config(),sort_keys=True).encode()).hexdigest()


def setup():
    cfg=resolved_config()
    base=json.loads((ROOT/cfg["v1_config"]).read_text())
    cfg={**base,**cfg, "liquidity_fraction":.001}
    window={key:pd.Timestamp(cfg[key],tz="UTC") for key in
            ("fit_start","validation_start","annual_cutoff","evaluation_end")}
    v1=(ROOT/cfg["v1_results"]).resolve()
    out=(ROOT/cfg["output_root"]).resolve()
    if out==v1 or v1 in out.parents:
        raise ValueError("V1 is frozen")
    out.mkdir(parents=True,exist_ok=True)
    return cfg,window,v1,out


def stage_path(v1,interval):
    return v1 if interval==60 else v1/"frequency"/f"{interval}m"/"20250701"


def cube(frame,times,symbols,column):
    key=pd.MultiIndex.from_product([times,symbols],names=["prediction_time","symbol"])
    return frame.set_index(["prediction_time","symbol"])[column].reindex(key).to_numpy().reshape(len(times),len(symbols))


def book(v1,symbols):
    frame=pd.read_parquet(v1/"derived/funding_events.parquet")
    cols=["event_time_ns","symbol","funding_rate","mark_price","bar_mark_proxy"]
    events=[]
    for ts,s,rate,mark,fallback in frame[cols].itertuples(index=False,name=None):
        is_proxy=pd.isna(mark)
        events.append((ts,symbols.index(s),rate,fallback if is_proxy else mark,int(is_proxy)))
    return sorted(set(events))


def execution_grid(frame,times,symbols):
    chosen=frame.loc[frame.prediction_time.isin(times)]
    grouped=chosen.groupby("prediction_time")
    target_clock=pd.DatetimeIndex(grouped.entry_time.first().reindex(times))
    exits=pd.DatetimeIndex(grouped.label_end_time.first().reindex(times))
    target_clock=target_clock.append(exits[-1:])
    execution=cube(chosen,times,symbols,"execution_price_proxy")
    terminal=cube(chosen,times,symbols,"terminal_execution_price_proxy")[-1:]
    return target_clock,np.vstack([execution,terminal])


def catalogue(cfg,dates,out):
    source=(ROOT/cfg["data_root"]).resolve()
    found=[]
    since=dates["evaluation_end"].date().isoformat()
    for symbol in cfg["symbols"]:
        p=symbol_dir(source,symbol)
        for kind,paths in (("1m_bar",(p/"data_type=bar/freq=1m").glob("date=*")),
                           ("funding",(p/"data_type=funding_rate").glob("freq=*/date=*"))):
            days=sorted({x.name[5:] for x in paths if x.name[5:]>=since and any(x.glob("*.parquet"))})
            found.append(dict(symbol=symbol,kind=kind,partition_days=len(days),
                              first_date=days[0] if days else "",last_date=days[-1] if days else "",
                              usage="POST_V1_RESERVED_METADATA_ONLY"))
    pd.DataFrame(found).to_csv(out/"post_v1_coverage.csv",index=False)
    pd.DataFrame([
        dict(start=cfg["fit_start"],end=cfg["validation_start"],use="V2_FIT_AND_CALIBRATION",seen_v1=True),
        dict(start=cfg["validation_start"],end=cfg["annual_cutoff"],use="V2_SELECTION_VALIDATION",seen_v1=True),
        dict(start=cfg["annual_cutoff"],end=cfg["evaluation_end"],use="V2_FROZEN_HISTORICAL_RESEARCH_V1_PREVIOUSLY_OBSERVED",seen_v1=True),
        dict(start=cfg["evaluation_end"],end="",use="RESERVED, DIRECTORY_METADATA_ONLY, NO_NINE_ASSET_ANNUAL_WINDOW",seen_v1=False),
    ]).to_csv(out/"data_usage_registry.csv",index=False)
