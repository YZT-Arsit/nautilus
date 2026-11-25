"""Bounded official-archive acquisition and metadata/settlement validation."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import warnings

import pandas as pd
import pyarrow.parquet as pq

from model_research.common import ROOT,settings
from v3.acquire import bar_partition, dates, download_bars, existing, fetch, funding
from v3.common import require_space, write_json
from feature_engine.data_sources.binance_vision import build_binance_vision_kline_url, read_binance_kline_zip
from v3.features import longest_run


def monthly(cfg,primary,extra,out,symbol,wanted):
    missing=[d for d in wanted if not existing(primary,symbol,d) and not existing(extra,symbol,d)]
    if len(missing) != len(wanted):
        return download_bars(cfg,primary,extra,out,symbol,wanted)
    key=wanted[0][:7]
    url=build_binance_vision_kline_url("futures_um",symbol,"1m","monthly",key)
    try:
        require_space(out,cfg)
        content=fetch(url)
        expected=fetch(url+".CHECKSUM",4096).decode().split()[0].lower()
        assert sha256(content).hexdigest()==expected
        data=pd.DataFrame(read_binance_kline_zip(content)).rename(columns={
                "quote_asset_volume":"quote_volume","number_of_trades":"trade_count",
                "taker_buy_base_asset_volume":"taker_buy_volume","taker_buy_quote_asset_volume":"taker_buy_quote_volume"})
        data["ts"]=pd.to_datetime(data.pop("open_time"),unit="ms")
        data["source"]="binance_vision_model_research_missing_partition"
        written=0
        for day,rows in data.groupby(data.ts.dt.strftime("%Y-%m-%d")):
            if day not in missing or existing(primary,symbol,day) or existing(extra,symbol,day):
                continue
            folder=bar_partition(extra,symbol,day)
            folder.mkdir(parents=True,exist_ok=True)
            tmp=folder/"part-0.tmp"
            rows.drop(columns="close_time").to_parquet(tmp,index=False,compression="zstd")
            tmp.replace(folder/"part-0.parquet")
            written+=1
        record=dict(symbol=symbol,key=key,frequency="monthly",days=written,bytes=len(content),
                    sha256=expected,status="OK",error="")
        return [record,*download_bars(cfg,primary,extra,out,symbol,wanted)]
    except (OSError,ValueError,AssertionError) as exc:
        record=dict(symbol=symbol,key=key,frequency="monthly",days=0,bytes=0,status="FAILED",error=str(exc))
        first=download_bars(cfg,primary,extra,out,symbol,missing[:1]) if missing else []
        if first and first[0]["status"]=="OK":
            return [record,*first,*download_bars(cfg,primary,extra,out,symbol,wanted)]
        return [record,*first]


def check_bars(primary, extra, symbol, day):
    roots = (primary,extra)
    for r in roots:
        files=sorted(bar_partition(r,symbol,day).glob("*.parquet"))
        if not files:
            continue
        chunks=[]
        for file in files:
            fields=pq.ParquetFile(file).schema.names
            p=pd.read_parquet(file,columns=["ts",*[c for c in ("trade_count","is_trade_bar") if c in fields]])
            if "trade_count" in p:
                p=p.loc[~p.trade_count.eq(0)]
            if "is_trade_bar" in p:
                p=p.loc[~p.is_trade_bar.eq(False)]
            chunks.append(pd.to_datetime(p.ts,utc=True))
        clock=pd.concat(chunks)
        date=pd.Timestamp(day,tz="UTC")
        inside=(clock>=date)&(clock<date+pd.Timedelta(days=1))&clock.eq(clock.dt.floor("min"))
        return int(clock[inside].nunique()),"primary" if r==primary else "supplemental"
    return 0,"missing"


def acquire(cfg,out):
    warnings.filterwarnings("ignore",message="Failed to parse line: open_time")
    wanted=dates(cfg["fresh_partition_start"],cfg["fresh_end"])
    primary=(ROOT/cfg["data_root"]).resolve()
    extra=out/"supplemental_market_data"
    known=[]
    for file in cfg["funding_mark_sources"]:
        p=ROOT.parents[1]/file
        if p.is_file():
            known.extend(json.loads(p.read_text(encoding="utf-8-sig")))
    def symbol_work(symbol):
        log=monthly(cfg,primary,extra,out,symbol,wanted)
        book=funding(cfg,primary,out,symbol,wanted,known)
        valid=[pd.Timestamp(e["fundingTime"],unit="ms",tz="UTC") for e in book
               if float(e["markPrice"])>0 and abs(float(e["fundingRate"]))<1]
        output=[]
        for day in wanted:
            stamp=pd.Timestamp(day,tz="UTC")
            next_day=stamp+pd.Timedelta(days=1)
            count,origin=check_bars(primary,extra,symbol,day)
            within=[t for t in valid if stamp<=t<next_day]
            left=max([t for t in valid if t<stamp],default=stamp)
            right=min([t for t in valid if t>=next_day],default=next_day)
            clocks=[left,*within,right]
            gap=max((b-a).total_seconds()/3600 for a,b in zip(clocks,clocks[1:]))
            output.append(dict(day=day,symbol=symbol,valid_minute_timestamps=count,bar_origin=origin,
                               official_settlements=len(within),max_funding_gap_hours=gap,
                               valid_day=count==1440 and bool(within) and gap<=12,
                               purpose="reserved_untouched" if day>=cfg["fresh_start"] else "warmup_only",
                               evidence="minute timestamp/traded flag and official settlements"))
        print(f"ACQUIRE {symbol} valid={sum(r['valid_day'] for r in output)}",flush=True)
        return log,output

    with ThreadPoolExecutor(max_workers=3) as pool:
        rec=list(pool.map(symbol_work,cfg["symbols"]))
    log_path=out/"download_log.csv"
    logs=[x for log,_ in rec for x in log]
    if logs:
        previous=pd.read_csv(log_path) if log_path.is_file() else pd.DataFrame()
        pd.concat([previous,pd.DataFrame(logs)],ignore_index=True).to_csv(log_path,index=False)
    calendar=pd.DataFrame([r for _,rows in rec for r in rows])
    common=calendar.groupby("day").valid_day.all()
    eligible=common.loc[common.index>=cfg["fresh_start"]]
    segment=longest_run(eligible)
    warmup=common.loc[common.index<cfg["fresh_start"]].all()
    prior=pd.read_csv((ROOT/cfg["v3_coverage"]).resolve())
    old=pd.DataFrame(dict(day=pd.to_datetime(prior.date,utc=True).dt.strftime("%Y-%m-%d"),symbol=prior.symbol,
           valid_minute_timestamps=prior.bar_rows,official_settlements=prior.funding_marks,
           valid_day=prior.common_valid_day,purpose="V3_PREVIOUSLY_OBSERVED",evidence="V3 verified receipt"))
    calendar["common_valid_day"]=calendar.day.map(common)
    pd.concat([old,calendar],ignore_index=True).to_csv(out/"fresh_data_coverage.csv",index=False)
    ready=len(segment)>=cfg["fresh_min_days"] and warmup and segment[0]==cfg["fresh_start"]
    write_json(out/"acquisition_receipt.json",dict(new_common_days=int(eligible.sum()),warmup_valid=bool(warmup),
               prior_seen_common_days=int(old.loc[old.valid_day,"day"].nunique()),
               chosen_days=segment,status="READY" if ready else "DATA_BLOCKED",
               note="Stage inspected exact minute timestamp grid and settlement coverage; no signal outcomes"))
    print("ACQUISITION_COMPLETE",ready,flush=True)


if __name__=="__main__":
    cfg,_,_,out=settings()
    acquire(cfg,out)
