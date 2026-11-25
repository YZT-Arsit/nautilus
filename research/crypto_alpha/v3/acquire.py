"""Only fill absent day partitions. Do not expose fresh prices or outcomes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
import hashlib
import json
import time
from urllib.request import urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
import warnings

import pandas as pd

from v3.common import ROOT,NAUTILUS,inputs,require_space,verify_prior,write_json
from data.market import symbol_dir
from feature_engine.data_sources.binance_vision import build_binance_vision_kline_url,read_binance_kline_zip

BAR="data_type=bar/freq=1m"
FUND="data_type=funding_rate"


def dates(start,end):
    first,last=map(date.fromisoformat,(start,end))
    return [(first+timedelta(days=i)).isoformat() for i in range((last-first).days)]


def fetch(url,limit=12_000_000):
    for attempt in range(3):
        try:
            with urlopen(url,timeout=24) as f:
                content=f.read(limit+1)
            if len(content)>limit:
                raise ValueError("response exceeds cap")
            return content
        except HTTPError as exc:
            if exc.code in (403,404,418,429,451) or attempt==2:
                raise
        except OSError:
            if attempt==2:
                raise
        time.sleep(1+attempt)


def bar_partition(root,symbol,day):
    return symbol_dir(root,symbol)/BAR/f"date={day}"


def existing(root,symbol,day):
    return any(bar_partition(root,symbol,day).glob("*.parquet"))


def download_bars(cfg,primary,extra,out,symbol,days):
    missing={day for day in days if not existing(primary,symbol,day) and not existing(extra,symbol,day)}
    records=[]
    july={d for d in days if d.startswith("2026-07-")}
    tasks=[("monthly","2026-07",july)] if july and july<=missing and len(july)==31 else []
    remaining=missing-july if tasks else missing.copy()

    def fetch_days(frequency,key,needed):
        require_space(out,cfg)
        url=build_binance_vision_kline_url("futures_um",symbol,"1m",frequency,key)
        content=fetch(url)
        expected=fetch(url+".CHECKSUM",4096).decode().split()[0].lower()
        checksum=hashlib.sha256(content).hexdigest()
        if expected!=checksum:
            raise ValueError("Binance archive checksum mismatch")
        frame=pd.DataFrame(read_binance_kline_zip(content)).rename(columns={
            "quote_asset_volume":"quote_volume","number_of_trades":"trade_count",
            "taker_buy_base_asset_volume":"taker_buy_volume","taker_buy_quote_asset_volume":"taker_buy_quote_volume"})
        frame["ts"]=pd.to_datetime(frame.pop("open_time"),unit="ms")
        frame["source"]="binance_vision_v3_missing_partition"
        grouped=frame.groupby(frame.ts.dt.strftime("%Y-%m-%d"))
        written=set()
        for day,df in grouped:
            if day not in needed or existing(primary,symbol,day) or existing(extra,symbol,day):
                continue
            p=bar_partition(extra,symbol,day)
            p.mkdir(parents=True,exist_ok=True)
            path=p/"part-0.parquet"
            temp=p/"part-0.tmp"
            df.drop(columns="close_time").to_parquet(temp,index=False,compression="zstd")
            temp.replace(path)
            written.add(day)
        records.append(dict(symbol=symbol,key=key,frequency=frequency,days=len(written),bytes=len(content),
                            sha256=checksum,status="OK",error=""))
        return needed-written

    for freq,key,subset in tasks:
        try:
            remaining|=fetch_days(freq,key,subset)
        except Exception as exc:
            remaining|=subset
            records.append(dict(symbol=symbol,key=key,frequency=freq,days=0,bytes=0,status="FAILED",error=str(exc)))
    for day in sorted(remaining):
        try:
            fetch_days("daily",day,{day})
        except Exception as exc:
            records.append(dict(symbol=symbol,key=day,frequency="daily",days=0,bytes=0,status="FAILED",error=str(exc)))
    print(f"{symbol} bar plan initially {len(missing)} missing days",flush=True)
    return records


def funding(cfg,primary,out,symbol,days,known):
    rows={int(x["fundingTime"]):x for x in known if x["symbol"]==symbol and x.get("markPrice")}
    # Retain official cached marks first and read existing canonical events with marks.
    for path in (symbol_dir(primary,symbol)/FUND).glob("freq=*/date=*/*.parquet"):
        if path.parent.name[5:] not in days:
            continue
        import pyarrow.parquet as pq
        if "mark_price" not in pq.ParquetFile(path).schema.names:
            continue
        frame=pd.read_parquet(path,columns=["ts","funding_rate","mark_price"])
        for r in frame.dropna(subset=["mark_price"]).itertuples():
            stamp=pd.Timestamp(r.ts).value//1_000_000
            rows.setdefault(stamp,dict(symbol=symbol,fundingTime=stamp,fundingRate=r.funding_rate,markPrice=r.mark_price))
    target=out/"supplemental_funding"/f"{symbol}.json"
    if target.is_file():
        rows.update({int(x["fundingTime"]):x for x in json.loads(target.read_text())})
    first=pd.Timestamp(days[0],tz="UTC")
    end=pd.Timestamp(days[-1],tz="UTC")+pd.Timedelta(days=1)
    # Most contracts settle at least once per eight hours. Daily coverage and
    # actual maximum gaps are checked before fresh evaluation.
    present={pd.Timestamp(ms,unit="ms",tz="UTC").date().isoformat() for ms in rows}
    missing=[day for day in days if day not in present]
    groups=[]
    for day in missing:
        if groups and date.fromisoformat(day)-date.fromisoformat(groups[-1][-1])==timedelta(days=1):
            groups[-1].append(day)
        else:
            groups.append([day])
    for group in groups:
        params=urlencode(dict(symbol=symbol,startTime=pd.Timestamp(group[0],tz="UTC").value//1_000_000,
                              endTime=(pd.Timestamp(group[-1],tz="UTC")+pd.Timedelta(days=1)).value//1_000_000-1,limit=1000))
        try:
            new=json.loads(fetch("https://fapi.binance.com/fapi/v1/fundingRate?"+params))
            if not isinstance(new,list) or len(new)>=1000 or any(x.get("symbol")!=symbol for x in new):
                raise ValueError("unexpected Funding response")
            rows.update({int(x["fundingTime"]):x for x in new if x.get("markPrice")})
        except Exception as exc:
            print(f"{symbol} funding gap {group[0]} to {group[-1]} {exc}",flush=True)
    output=[rows[k] for k in sorted(rows) if first.value//1_000_000<=k<end.value//1_000_000]
    target.parent.mkdir(exist_ok=True)
    write_json(target,output)
    return output


def acquire():
    cfg,v1,v2,out=inputs()
    warnings.filterwarnings("ignore",message="Failed to parse line: open_time")
    done=verify_prior(v1,v2)
    write_json(out/"prior_freeze.json",done)
    primary=(ROOT/cfg["data_root"]).resolve()
    supplemental=out/"supplemental_market_data"
    wanted=dates(cfg["fresh_start"],cfg["fresh_end_exclusive"])
    floor=pd.Timestamp(cfg["fit_start"]).date().isoformat()
    cutoff=cfg["historical_end"]
    candidates=[]
    parent=symbol_dir(primary,"ZZ").parent
    for x in parent.glob("symbol=*"):
        days={p.name[5:] for p in (x/BAR).glob("date=*") if any(p.glob("*.parquet"))}
        history=sorted(d for d in days if floor<=d<cutoff)
        candidates.append(dict(symbol=x.name[7:],history_partition_days=len(history),
                               earliest_partition=min(days,default=""),latest_partition=max(days,default=""),
                               fresh_partition_days=sum(d in days for d in wanted)))
    pd.DataFrame(candidates).to_csv(out/"universe_catalogue.csv",index=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(download_bars,cfg,primary,supplemental,out,s,wanted) for s in cfg["symbols"]]
        log=[row for result in tasks for row in result.result()]
    pd.DataFrame(log).to_csv(out/"acquisition_log.csv",index=False)
    known=[]
    for p in cfg["funding_mark_sources"]:
        path=NAUTILUS/p
        if path.is_file():
            known.extend(json.loads(path.read_text(encoding="utf-8-sig")))
    rows=[]
    for symbol in cfg["symbols"]:
        settlements=funding(cfg,primary,out,symbol,wanted,known)
        days_with_marks={pd.Timestamp(x["fundingTime"],unit="ms",tz="UTC").date().isoformat() for x in settlements}
        for day in wanted:
            rows.append(dict(day=day,symbol=symbol,primary_bar=existing(primary,symbol,day),
                             supplemental_bar=existing(supplemental,symbol,day),funding_mark=day in days_with_marks))
    index=pd.DataFrame(rows)
    index["both"]=index.funding_mark&(index.primary_bar|index.supplemental_bar)
    counts=index.groupby("day").both.transform("sum")
    index["common_partition_day"]=counts==len(cfg["symbols"])
    index.to_csv(out/"partition_coverage.csv",index=False)
    print(f"DATA_ACQUISITION_COMPLETE common_days={index.loc[index.common_partition_day,'day'].nunique()}",flush=True)


if __name__=="__main__":
    acquire()
