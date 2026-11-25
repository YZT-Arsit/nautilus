"""Exercise the full workflow on three two-week slices of the real panel."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parents[1])]
from training.run import code_version, complete_frequency, funding_inputs
from training.prepare import configuration
from training.protocol import Panel, annual_windows
import torch
from threadpoolctl import threadpool_limits


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=ROOT/"configs/v1.json")
    parser.add_argument("--output",type=Path)
    parsed=parser.parse_args()
    cfg,_,out=configuration(parsed.config,parsed.output)
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    cfg.update(epochs=2,patience=2)
    frame=pd.read_parquet(out/"derived/panel_60.parquet")
    start,end=(pd.Timestamp(cfg[key],tz="UTC") for key in ("start","end"))
    window=next(annual_windows(start,end,cfg["fit_years"],cfg["validation_months"]))
    blocks=[]
    for first in (window.train_start,window.validation_start,window.test_start):
        blocks.append(frame.loc[(frame.prediction_time>=first)&(frame.prediction_time<first+pd.Timedelta(days=14))])
    registry=pd.read_csv(out/"factor_registry.csv")
    factors=registry.loc[registry.enabled,"factor_name"]
    panel=Panel(pd.concat(blocks),cfg["symbols"],factors,cfg["min_assets"])
    folder=out.parent/"sanity_60m"
    folder.mkdir(exist_ok=True)
    window=replace(window,test_end=window.test_start+pd.Timedelta(days=14))
    with threadpool_limits(limits=cfg["threads"]):
        complete_frequency(cfg,panel,window,60,folder,*funding_inputs(out/"derived"),code_version())
    print("SANITY_COMPLETE")
