"""Build V3 features on causal hourly panels and reproduce V2 inputs on fresh bars."""
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd

from v3.common import ROOT
from data.market import read_bars
from factors.library import compute, add_cross_section
from v2.features import local_features, cross

HORIZONS=(30,60,120,240,480,1440)
GROUPS=("Price/momentum","Reversal","Volatility","Volume","Order flow","Cross-sectional","Derivatives")


def group_name(category):
    return {
        "momentum":"Price/momentum","microstructure":"Price/momentum","reversal":"Reversal",
        "volatility":"Volatility","liquidity":"Volume","order_flow":"Order flow","flow":"Order flow",
        "cross_section":"Cross-sectional","funding":"Derivatives","derivatives":"Derivatives",
    }.get(category,"Price/momentum")


def enrich(frame, registry):
    frame=frame.sort_values(["prediction_time","symbol"]).copy()
    order=frame.pivot(index="prediction_time",columns="symbol",values="ret_60m")
    btc=order["BTCUSDT"]
    martingale=order.sub(order.rolling(72,min_periods=48).cov(btc).div(btc.rolling(72,min_periods=48).var(),axis=0)
                         .shift(1).mul(btc,axis=0))
    keys=pd.MultiIndex.from_frame(frame[["prediction_time","symbol"]])
    def flatten(x):
        return pd.Series(x.stack().reindex(keys).to_numpy(),index=frame.index)
    grp=frame.groupby("prediction_time")
    residual=flatten(martingale)
    mom=flatten(martingale.rolling(4).sum())
    rv=flatten(martingale.rolling(24,min_periods=18).std(ddof=0))
    fund=frame.funding_last-grp.funding_last.transform("median")
    liq=frame.relative_qvol/grp.relative_qvol.transform("median")
    rows=[
      ("v3_resid_1h",residual,"R1h - beta_previous * BTC_R1h","72h","ret_60m,BTC close","Price/momentum"),
      ("v3_resid_4h",mom,"sum of last four hourly returns hedged at prior betas","76h","v3_resid_1h","Price/momentum"),
      ("v3_resid_reversal",-residual/rv,"negative current residual divided by trailing residual volatility","96h","v3_resid_1h","Reversal"),
      ("v3_resid_strength",mom/rv,"four-hour residual drift divided by trailing residual volatility","96h","v3_resid_4h,v3_resid_1h","Cross-sectional"),
      ("v3_flow_signed_repricing",frame.v2_flow_decay*frame.v2_flow_persistence.abs()-frame.ret_60m/frame.rv_120m,
       "linearly-weighted flow times signed persistence magnitude minus volatility-normalized return","120m",
       "v2_flow_decay,v2_flow_persistence,ret_60m,rv_120m","Order flow"),
      ("v3_high_volume_resid",residual*np.log(liq),"residual return times log relative volume surprise","72h",
       "v3_resid_1h,relative_qvol","Volume"),
      ("v3_liquidity_stress",np.log(liq)/frame.v2_intensity_variation,
       "relative quote-volume shock divided by trade-count coefficient of variation","24h","relative_qvol,v2_intensity_variation","Volume"),
      ("v3_funding_resid_crowd",fund*mom/rv,"relative last settled funding times residual risk-adjusted momentum","96h",
       "funding_last,v3_resid_4h,v3_resid_1h","Derivatives"),
    ]
    extra=[]
    for name,x,formula,w,fields,category in rows:
        frame[name]=x.replace([np.inf,-np.inf],np.nan)
        extra.append(dict(factor_name=name,formula=formula,lookback=w,input_fields=fields,
                          source="Nautilus completed 1m bars and official settled Funding via causal V1/V2 transforms",
                          availability_time="completed at prediction; beta shifted one hour",category=category,
                          implementation_version="v3",enabled=True,group=category))
    metadata=registry.copy()
    metadata["group"]=metadata.category.map(group_name)
    return frame,pd.concat([metadata,pd.DataFrame(extra)],ignore_index=True)


def historical(v2):
    panel=pd.read_parquet(v2/"derived/research_panel.parquet")
    return enrich(panel,pd.read_csv(v2/"factor_registry.csv"))


def longest_run(daily):
    best=[]
    current=[]
    for day,valid in daily.items():
        if valid:
            current.append(day)
            if len(current)>len(best):
                best=current.copy()
        else:
            current=[]
    return best


def fresh_panel(cfg,v2,out):
    start=pd.Timestamp(cfg["fresh_start"],tz="UTC")
    end=pd.Timestamp(cfg["fresh_end_exclusive"],tz="UTC")
    warm=start-pd.Timedelta(days=8)
    primary=(ROOT/cfg["data_root"]).resolve()
    original_extra=(ROOT/cfg["supplemental_bar_root"]).resolve()
    fresh_extra=out/"supplemental_market_data"
    old_rates=pd.read_parquet((ROOT/cfg["v1_results"]).resolve()/"derived/funding_events.parquet")
    f_parts=[]
    b_parts=[]
    fund_book=[]
    coverage=[]
    for pos,symbol in enumerate(cfg["symbols"]):
        early,_,_=read_bars(primary,symbol,warm,start-pd.Timedelta(nanoseconds=1),warm,original_extra)
        late,_,_=read_bars(primary,symbol,start,end-pd.Timedelta(nanoseconds=1),start,fresh_extra)
        bars=pd.concat([early,late])
        if bars.index.duplicated().any():
            raise ValueError("duplicate minute")
        archive=json.loads((out/"supplemental_funding"/f"{symbol}.json").read_text())
        records={}
        early_rates=old_rates.loc[(old_rates.symbol==symbol)&(old_rates.event_time_ns>=warm.value)&(old_rates.event_time_ns<start.value)]
        for r in early_rates.itertuples():
            records[r.event_time_ns]=(r.funding_rate,r.mark_price)
        for r in archive:
            records[int(r["fundingTime"])*1_000_000]=(float(r["fundingRate"]),float(r["markPrice"]))
        events=[SimpleNamespace(event_time_ns=ns,funding_rate=rate,mark_price=mark)
                for ns,(rate,mark) in sorted(records.items())]
        fund_book.extend((e.event_time_ns,pos,e.funding_rate,e.mark_price,0) for e in events)
        bars_by_day=late.close.groupby((late.index-pd.Timedelta(minutes=1)).floor("D")).count()
        settlement_times=pd.to_datetime([e.event_time_ns for e in events if start.value<=e.event_time_ns<end.value],unit="ns",utc=True)
        for day in pd.date_range(start,end,freq="D",inclusive="left"):
            ns=np.array([e.event_time_ns for e in events])
            in_day=settlement_times[(settlement_times>=day)&(settlement_times<day+pd.Timedelta(days=1))]
            left=ns[ns<=day.value]
            right=ns[ns>=day.value+86400*10**9]
            vicinity=np.r_[left[-1:],in_day.asi8,right[:1]]
            gap_ok=bool(len(left) and (len(right) or day+pd.Timedelta(days=1)==end) and
                        len(in_day)>0 and np.max(np.diff(vicinity),initial=0)<=12*3600*10**9)
            # At the final boundary, also bound distance to last observed event.
            if len(in_day) and (day+pd.Timedelta(days=1)).value-in_day[-1].value>12*3600*10**9:
                gap_ok=False
            count=int(bars_by_day.get(day,0))
            coverage.append(dict(symbol=symbol,date=day,bar_rows=count,funding_marks=len(in_day),funding_gap_ok=gap_ok,
                                 eligible=count>=1433 and gap_ok))
        half=pd.date_range(warm,end,freq="30min",inclusive="left")
        base,_=compute(bars,half,events)
        base["prediction_time"]=half
        base["symbol"]=symbol
        b_parts.append(base.reset_index(drop=True))
        hours=half[::2]
        ext,_=local_features(bars,hours,events)
        ext["prediction_time"]=hours
        ext["symbol"]=symbol
        ext["eligible"]=bars.close.reindex(hours).notna().to_numpy()
        ext["feature_time"]=hours.where(ext.eligible)
        ext["max_feature_available_time"]=ext["feature_time"]
        prices=bars.open.copy()
        prices.index-=pd.Timedelta(minutes=1)
        entry=hours+pd.Timedelta(minutes=cfg["delay_minutes"])
        ext["execution_price_proxy"]=prices.reindex(entry).to_numpy()
        y0=bars.close.reindex(entry).to_numpy()
        for interval in HORIZONS:
            cutoff=entry+pd.Timedelta(minutes=interval)
            ext[f"ret_label_{interval}"]=bars.close.reindex(cutoff).to_numpy()/y0-1
            ext[f"terminal_{interval}"]=prices.reindex(cutoff).to_numpy()
        f_parts.append(ext.reset_index(drop=True))
    coverage=pd.DataFrame(coverage)
    valid=coverage.groupby("date").eligible.all()
    days=longest_run(valid)
    coverage["common_valid_day"]=coverage.date.map(valid)
    coverage["selected_window"]=coverage.date.isin(days)
    coverage.to_csv(out/"data_coverage.csv",index=False)
    if len(days)<cfg["fresh_min_days"]:
        raise ValueError(f"common continuous fresh coverage only {len(days)} days")
    base=add_cross_section(pd.concat(b_parts,ignore_index=True),[])
    frame=base.merge(pd.concat(f_parts,ignore_index=True),on=["prediction_time","symbol"],validate="one_to_one")
    cross(frame)
    frame,metadata=enrich(frame,pd.read_csv(v2/"factor_registry.csv"))
    return frame,metadata,sorted(fund_book),days[0],days[-1]+pd.Timedelta(days=1)
