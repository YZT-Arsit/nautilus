"""Reconcile missing official funding records with curl using system TLS trust."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from urllib.parse import urlencode

import pandas as pd


def relay(cache,start,end):
    logs=[]
    folder=cache/"supplemental_funding"
    days=pd.date_range(start,end,inclusive="left").strftime("%Y-%m-%d").tolist()

    def fill(path):
        symbol=path.stem
        old=json.loads(path.read_text())
        timestamps={int(r["fundingTime"]):r for r in old if r.get("markPrice")}
        need=[]
        for day in days:
            anchor=pd.Timestamp(day,tz="UTC").value//1_000_000
            known=sum(anchor<=t<anchor+86_400_000 for t in timestamps)
            if known<3:
                if need and date.fromisoformat(day)-date.fromisoformat(need[-1][-1])==timedelta(days=1):
                    need[-1].append(day)
                else:
                    need.append([day])
        details=[]
        for g in need:
            lo=pd.Timestamp(g[0],tz="UTC").value//1_000_000
            hi=(pd.Timestamp(g[-1],tz="UTC")+pd.Timedelta(days=1)).value//1_000_000-1
            url="https://fapi.binance.com/fapi/v1/fundingRate?"+urlencode(
                dict(symbol=symbol,startTime=lo,endTime=hi,limit=1000))
            data=subprocess.check_output(["curl","--fail","--silent","--show-error","--max-time","30","--retry","2",url])
            rows=json.loads(data)
            if not isinstance(rows,list) or len(rows)>=1000 or not rows:
                raise ValueError(f"official endpoint returned invalid funding coverage for {symbol}")
            n=0
            for row in rows:
                ms=int(row["fundingTime"])
                if row["symbol"]!=symbol or not lo<=ms<=hi or float(row["markPrice"])<=0 or abs(float(row["fundingRate"]))>=1:
                    raise ValueError(f"invalid settlement {symbol}")
                if ms not in timestamps:
                    n+=1
                    timestamps[ms]=row
            details.append(dict(symbol=symbol,requested_dates=[g[0],g[-1]],new_records=n,archive=url,
                                response_sha256=sha256(data).hexdigest()))
        path.write_text(json.dumps([timestamps[t] for t in sorted(timestamps)],indent=2),encoding="utf-8")
        return details

    with ThreadPoolExecutor(max_workers=3) as pool:
        for rows in pool.map(fill,sorted(folder.glob("*.json"))):
            logs.extend(rows)
    (cache/"funding_relay_receipt.json").write_text(json.dumps(logs,indent=2),encoding="utf-8")
    print(sum(r["new_records"] for r in logs),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("cache",type=Path)
    p.add_argument("start")
    p.add_argument("end")
    a=p.parse_args()
    relay(a.cache,a.start,a.end)
