"""Incremental features evaluated on completed 1-minute Nautilus bars."""
import hashlib
import json

import numpy as np
import pandas as pd

from data.market import marks_archive, read_bars, read_funding
from labels.alignment import normalize
from v2.common import NAUTILUS, ROOT, config_digest


def local_features(b, clock, fund):
    close=b.close
    quote=b.quote_volume
    trades=b.trade_count
    flow=(2*b.taker_buy_quote_volume-quote)/quote.replace(0,np.nan)
    r=np.log(close).diff()
    high,low,opening=b.high,b.low,b.open
    intensity=trades.rolling(60).mean()
    volume=quote.rolling(60).sum()
    features={}
    registry=[]
    def put(name,value,formula,period,fields,category):
        features[name]=value if value.index.equals(clock) else value.reindex(clock)
        registry.append(dict(factor_name=name,formula=formula,lookback=period,input_fields=fields,
                             source="Nautilus Binance StandardBar or official funding",availability_time="completed at prediction",
                             category=category,implementation_version="v2",enabled=True))
    w=120
    # Each rolling minute operation requires contiguous valid observations.
    put("v2_flow_decay",flow.rolling(60).apply(np.dot,raw=True,args=(np.arange(1,61)/1830,)),
        "linearly weighted mean of signed quote imbalance",60,"taker_buy_quote_volume,quote_volume","flow")
    put("v2_flow_surprise",(flow-flow.rolling(w).mean())/flow.rolling(w).std(),
        "current imbalance minus rolling mean divided by rolling std",w,"taker_buy_quote_volume,quote_volume","flow")
    put("v2_flow_persistence",np.sign(flow).rolling(w).mean(),
        "mean of imbalance signs",w,"taker_buy_quote_volume,quote_volume","flow")
    put("v2_flow_price_divergence",flow.rolling(60).mean()-np.sign(r.rolling(60).sum())*.25,
        "average signed imbalance minus quarter sign of recent price change",60,"taker_buy_quote_volume,quote_volume,close","flow")
    put("v2_flow_dispersion",flow.rolling(w).std(),
        "rolling std of signed quote imbalance",w,"taker_buy_quote_volume,quote_volume","flow")
    put("v2_flow_vol_corr",flow.abs().rolling(w).corr(r.abs()),
        "corr(abs imbalance,abs return)",w,"taker_buy_quote_volume,quote_volume,close","flow")
    put("v2_taker_quote_shock",b.taker_buy_quote_volume.rolling(15).sum()/b.taker_buy_quote_volume.rolling(w).sum(),
        "short taker buy notional share of long window",w,"taker_buy_quote_volume","flow")
    put("v2_intensity_variation",trades.rolling(w).std()/trades.rolling(w).mean(),
        "trade count coefficient of variation",w,"trade_count","liquidity")
    put("v2_intensity_trend",intensity/intensity.shift(60)-1,
        "trade counts last hour versus preceding hour",120,"trade_count","liquidity")
    put("v2_size_volatility",(quote/trades.replace(0,np.nan)).rolling(w).std()/(quote/trades.replace(0,np.nan)).rolling(w).mean(),
        "trade notional per trade rolling coefficient of variation",w,"quote_volume,trade_count","liquidity")
    put("v2_volume_concentration",quote.rolling(60).max()/volume,
        "largest minute quote volume divided by hourly total",60,"quote_volume","liquidity")
    put("v2_turnover_volatility",np.log1p(volume).diff(60).rolling(w).std(),
        "std of lagged hourly log-volume change",239,"quote_volume","liquidity")
    put("v2_range_per_notional",(np.log(high/low)/(quote.replace(0,np.nan)/1e6)).rolling(60).mean(),
        "mean intraminute log range per million quote volume",60,"high,low,quote_volume","liquidity")
    put("v2_gk_vol",np.sqrt((.5*np.log(high/low)**2-(2*np.log(2)-1)*np.log(close/opening)**2).rolling(w).sum().clip(lower=0)),
        "Garman-Klass volatility",w,"OHLC","volatility")
    put("v2_signed_semivariance",((r.clip(lower=0)**2-r.clip(upper=0)**2).rolling(w).sum())/(r**2).rolling(w).sum(),
        "upside minus downside squared variation divided by total",w,"close","volatility")
    put("v2_bipower_jump",1-(np.pi/2)*(r.abs()*r.shift().abs()).rolling(w).sum()/(r**2).rolling(w).sum(),
        "one minus bipower variation divided by realized variance",121,"close","volatility")
    put("v2_drawup",np.log(close/close.rolling(240).min()),
        "log close divided by rolling minimum close",240,"close","momentum")
    put("v2_advancement",r.rolling(60).sum()/r.abs().rolling(60).sum(),
        "directional return divided by realized path length",60,"close","momentum")
    put("v2_vol_adjusted_curvature",(r.rolling(60).sum()-.25*r.rolling(240).sum())/np.sqrt((r*r).rolling(240).sum()),
        "short return minus scaled long return divided by realized variance root",240,"close","momentum")
    put("v2_close_in_range",((2*close-high-low)/(high-low).replace(0,np.nan)).rolling(60,min_periods=50).mean(),
        "rolling mean of close-location value",60,"OHLC","reversal")
    etimes=pd.to_datetime([e.event_time_ns for e in fund],utc=True,unit="ns")
    rates=pd.Series([e.funding_rate for e in fund],index=etimes)
    if rates.empty:
        rates=pd.Series(index=pd.DatetimeIndex([],tz="UTC"),dtype=float)
    rolling=rates.rolling(3,min_periods=2)
    for name,series,formula in (
        ("v2_funding_average",rolling.mean(),"mean of up to three prior published settlements"),
        ("v2_funding_volatility",rolling.std(),"std of up to three prior published settlements"),
        ("v2_funding_shock",rates-rolling.mean().shift(),"last rate minus preceding rolling mean")):
        observed=series.reindex(clock-pd.Timedelta(nanoseconds=1),method="ffill",tolerance=pd.Timedelta(hours=16))
        observed.index=clock
        put(name,observed,formula,"3 events","official funding rate","funding")
    return pd.DataFrame(features,index=clock).replace([np.inf,-np.inf],np.nan),registry


def cross(frame):
    grouped=frame.groupby("prediction_time")
    entries=[
        ("v2_xs_reversal", .5-grouped.ret_30m.rank(pct=True),"negative cross-sectional 30m return percentile","ret_30m","30m"),
        ("v2_xs_liquidity_shock",grouped.relative_qvol.rank(pct=True),"rank of traded notional relative to own history","relative_qvol","24h"),
        ("v2_xs_residual_reversal",.5-grouped.btc_residual.rank(pct=True),"negative residual-return percentile","btc_residual","24h"),
        ("v2_xs_funding_relative",frame.funding_last-grouped.funding_last.transform("median"),
         "last funding minus section median","funding_last","latest"),
        ("v2_xs_beta_relative",frame.btc_beta-grouped.btc_beta.transform("median"),
         "rolling BTC beta minus section median","btc_beta","24h"),
        ("v2_xs_risk_momentum",grouped.risk_adjusted_mom.rank(pct=True),
         "cross-section percentile of risk adjusted momentum","risk_adjusted_mom","120m")]
    registry=[]
    for name,x,formula,fields,period in entries:
        frame[name]=x
        registry.append(dict(factor_name=name,formula=formula,lookback=period,input_fields=fields,
                             source="causal V1 features",availability_time="prediction",category="cross_section",
                             implementation_version="v2",enabled=True))
    return registry


def prepare(cfg,dates,v1,out):
    cache=out/"derived"
    cache.mkdir(exist_ok=True)
    v1_frame=pd.read_parquet(v1/"derived/panel_60.parquet")
    basic=pd.read_csv(v1/"factor_registry.csv")
    fields=["prediction_time","symbol","feature_time","max_feature_available_time","eligible"]
    v1_frame=v1_frame[fields+basic.factor_name.tolist()]
    clock=pd.DatetimeIndex(sorted(v1_frame.prediction_time.unique()))
    start=dates["fit_start"]
    end=dates["evaluation_end"]
    raw_root=(ROOT/cfg["data_root"]).resolve()
    extra=(ROOT/cfg["supplemental_bar_root"]).resolve()
    archive=marks_archive([NAUTILUS/p for p in cfg["funding_mark_sources"]])
    built=[]
    for symbol in cfg["symbols"]:
        print(f"new factors and horizon prices {symbol}",flush=True)
        last_before_end=end-pd.Timedelta(nanoseconds=1)
        minute,_,_=read_bars(raw_root,symbol,start-pd.Timedelta(days=4),last_before_end,start,extra)
        funding,_=read_funding(raw_root,symbol,start-pd.Timedelta(days=4),last_before_end,archive)
        result,registry=local_features(minute,clock,funding)
        result["prediction_time"]=clock
        result["symbol"]=symbol
        entry=clock+pd.Timedelta(minutes=cfg["delay_minutes"])
        base=minute.close.reindex(entry).to_numpy()
        immediate=minute.close.reindex(clock).to_numpy()
        traded=minute.open.copy()
        traded.index-=pd.Timedelta(minutes=1)
        result["execution_price_proxy"]=traded.reindex(entry).to_numpy()
        for horizon in cfg["horizons_minutes"]:
            offset=pd.Timedelta(minutes=horizon)
            exit=entry+offset
            p=minute.close.reindex(exit).to_numpy()
            result[f"ret_label_{horizon}"]=np.where(exit<=end,p/base-1,np.nan)
            result[f"immediate_{horizon}"]=minute.close.reindex(clock+offset).to_numpy()/immediate-1
            result[f"terminal_{horizon}"]=traded.reindex(exit).to_numpy()
        built.append(result.reset_index(drop=True))
    features=v1_frame.merge(pd.concat(built,ignore_index=True),on=["prediction_time","symbol"],validate="one_to_one")
    registry.extend(cross(features))
    merged=pd.concat([basic,pd.DataFrame(registry)],ignore_index=True)
    merged.to_csv(out/"factor_registry.csv",index=False)
    features.to_parquet(cache/"research_panel.parquet",index=False,compression="zstd")
    (cache/"prepared.json").write_text(json.dumps({"config_sha256":config_digest(),
                                                  "features":len(merged),"v1_input_manifest_sha256":hashlib.sha256((v1/"artifact_manifest.json").read_bytes()).hexdigest()}))
    return features,merged.factor_name.tolist()


def horizon_panel(frame,interval,horizon,delay,minimum,epsilon):
    selected=frame.loc[(frame.prediction_time.dt.hour*60+frame.prediction_time.dt.minute)%interval==0].copy()
    selected["label_raw"]=selected[f"ret_label_{horizon}"].where(selected.eligible)
    selected["entry_time"]=selected.prediction_time+pd.Timedelta(minutes=delay)
    selected["label_end_time"]=selected.entry_time+pd.Timedelta(minutes=horizon)
    selected["label_available_time"]=selected.label_end_time
    selected["terminal_execution_price_proxy"]=selected[f"terminal_{interval}"]
    return normalize(selected,minimum,epsilon)
