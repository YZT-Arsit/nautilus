"""Invoke the existing Nautilus importer only for configured absent daily partitions."""
import argparse
from datetime import date,timedelta
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parents[1])]
from data.market import symbol_dir
from training.prepare import configuration


def supplement(cfg, primary, python):
    if not cfg.get("supplemental_bar_root"):
        raise ValueError("configure the supplemental output Hive root")
    extra=(ROOT/cfg["supplemental_bar_root"]).resolve()
    if extra==primary or primary in extra.parents:
        raise ValueError("supplemental output must be independent of the read-only catalogue")
    for block in cfg["missing_partition_ranges"]:
        start,end=map(date.fromisoformat,(block["start"],block["end"]))
        for symbol in block["symbols"]:
            pending=start
            while pending<=end:
                day=pending.isoformat()
                relative=Path(f"data_type=bar/freq=1m/date={day}")
                paths=[symbol_dir(root,symbol)/relative for root in (primary,extra)]
                if not any(any(p.glob("*.parquet")) for p in paths):
                    command=[python,"-B",str(ROOT.parents[1]/"scripts/ingest_binance_vision.py"),
                             "--market","futures_um","--symbol",symbol,"--data-type","klines",
                             "--interval","1m","--frequency","daily","--start",day,"--end",day,
                             "--output",str(extra),"--overwrite","--timeout","20"]
                    subprocess.run(command,check=True)
                    if not any(paths[1].glob("*.parquet")):
                        raise RuntimeError(f"importer did not produce {symbol} {day}")
                pending+=timedelta(days=1)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=ROOT/"configs/v1.json")
    args=parser.parse_args()
    cfg,root,_=configuration(args.config)
    supplement(cfg,root,sys.executable)
