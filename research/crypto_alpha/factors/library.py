"""Completed-minute factors and a separate daily Alpha158 core."""
import numpy as np
import pandas as pd


def compute(bar, clock, events=()):
    values, registry = {}, []
    c, o, h, l, v, q, n, buy, buyq = (bar[col] for col in
        ("close","open","high","low","volume","quote_volume","trade_count",
         "taker_buy_volume","taker_buy_quote_volume"))
    r = c.pct_change(fill_method=None)
    logret = np.log(c).diff()
    bar_mean = lambda s, w: s.rolling(w).mean()
    bar_sum = lambda s, w: s.rolling(w).sum()
    bar_std = lambda s, w: s.rolling(w).std(ddof=0)
    def add(name, series, formula, window, fields, category, source="Crypto V1"):
        values[name] = series.reindex(clock).replace([np.inf, -np.inf], np.nan)
        registry.append(dict(factor_name=name, formula=formula, lookback=window, input_fields=fields,
                             source=source, availability_time="at completed inputs", category=category,
                             implementation_version="v1"))
    returns = {}
    for mins in (5,15,30,60,120,240,480,1440):
        returns[mins] = c / c.shift(mins) - 1
        returns[mins] = returns[mins].where(c.notna().rolling(mins+1).sum().eq(mins+1))
        add(f"ret_{mins}m", returns[mins], f"close/close[t-{mins}]-1", f"{mins}m", "close","momentum")
    add("ma_dev_60m",c/bar_mean(c,60)-1,"close/SMA(close,60)-1","60m","close","momentum")
    add("ma_dev_8h",c/bar_mean(c,480)-1,"close/SMA(close,480)-1","480m","close","momentum")
    add("mom_accel",returns[30]-(returns[240]/4),"R30-R240/4","240m","close","momentum")
    add("up_fraction",bar_mean((r>0).where(r.notna()),120),"mean(1[minute return>0],120)","120m","close","momentum")
    add("path_efficiency",returns[60].abs()/bar_sum(r.abs(),60),"abs(R60)/sum(abs(r),60)","60m","close","momentum")
    add("ret_autocorr",r.rolling(240).corr(r.shift(1)),"corr(r,lag(r),240)","241m","close","reversal")
    add("price_z", (c-bar_mean(c,120))/bar_std(c,120),"(close-SMA(close,120))/std(close,120)","120m","close","reversal")
    add("price_quantile",c.rolling(120).rank(pct=True),"rolling percentile rank(close,120)","120m","close","reversal")
    hi,lo=h.rolling(240).max(),l.rolling(240).min()
    add("high_dist",c/hi-1,"close/rolling_max(high,240)-1","240m","high,close","reversal")
    add("low_dist",c/lo-1,"close/rolling_min(low,240)-1","240m","low,close","reversal")
    add("range_pos",(c-lo)/(hi-lo),"(close-min(low,240))/(max(high,240)-min(low,240))","240m","OHLC","reversal")
    gain,loss=bar_sum(r.clip(lower=0),60),bar_sum((-r).clip(lower=0),60)
    add("rsi_60m",gain/(gain+loss),"sum(r+,60)/(sum(r+,60)+sum(r-,60))","60m","close","reversal")
    rv={}
    for mins in (30,120,1440):
        rv[mins]=np.sqrt(bar_sum(logret**2,mins))
        add(f"rv_{mins}m",rv[mins],f"sqrt(sum(log_return^2,{mins}))",f"{mins}m","close","volatility")
    add("rv_ratio",rv[30]/rv[1440],"RV30/RV1440","1440m","close","volatility")
    add("true_range_60",bar_mean(pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1,skipna=False),60)/c,
        "mean(max(H-L,abs(H-Cprev),abs(L-Cprev)),60)/C","61m","OHLC","volatility")
    add("parkinson",np.sqrt(bar_mean(np.log(h/l)**2,120)/(4*np.log(2))),"sqrt(mean(log(H/L)^2,120)/(4*ln(2)))","120m","high,low","volatility")
    add("downside",np.sqrt(bar_sum(logret.clip(upper=0)**2,120)),"sqrt(sum(min(log_return,0)^2,120))","120m","close","volatility")
    add("risk_adjusted_mom",returns[120]/rv[120],"R120/RV120","120m","close","volatility")
    add("return_skew",r.rolling(240).skew(),"rolling skew(r,240)","240m","close","volatility")
    add("return_kurtosis",r.rolling(240).kurt(),"rolling excess kurtosis(r,240)","240m","close","volatility")
    add("vol_clustering",(r.abs()).rolling(240).corr(r.abs().shift()),"corr(abs(r),lag(abs(r)),240)","241m","close","volatility")
    add("log_base_volume",np.log1p(bar_sum(v,60)),"ln(1+sum(base volume,60))","60m","volume","liquidity")
    add("log_quote_volume",np.log1p(bar_sum(q,60)),"ln(1+sum(quote volume,60))","60m","quote_volume","liquidity")
    add("relative_qvol",bar_mean(q,60)/bar_mean(q,1440),"mean(quote volume,60)/mean(quote volume,1440)","1440m","quote_volume","liquidity")
    add("qvol_change",bar_sum(q,60)/bar_sum(q,60).shift(60)-1,"sum(q,60)/lag(sum(q,60),60)-1","120m","quote_volume","liquidity")
    add("log_trade_count",np.log1p(bar_sum(n,60)),"ln(1+sum(trades,60))","60m","trade_count","liquidity")
    add("trade_size",bar_sum(q,60)/bar_sum(n,60),"sum(quote volume,60)/sum(trades,60)","60m","quote_volume,trade_count","liquidity")
    add("vwap_dev",c/(bar_sum(q,60)/bar_sum(v,60))-1,"close/(sum(quote volume,60)/sum(base volume,60))-1","60m","close,volume,quote_volume","liquidity")
    add("volume_weighted_return",bar_sum(r*q,60)/bar_sum(q,60),"sum(r*quote volume,60)/sum(quote volume,60)","60m","close,quote_volume","liquidity")
    add("amihud",bar_mean(r.abs()/q.where(q>0),120)*1e6,"1e6*mean(abs(r)/quote volume,120)","120m","close,quote_volume","liquidity")
    add("px_volume_corr",r.rolling(240).corr(np.log1p(q).diff()),"corr(r,diff(ln(1+quote volume)),240)","240m","close,quote_volume","liquidity")
    add("liquidity_dispersion",bar_std(np.log1p(q),120),"std(ln(1+quote volume),120)","120m","quote_volume","liquidity")
    add("trade_intensity",bar_mean(n,15)/bar_mean(n,240),"mean(trades,15)/mean(trades,240)","240m","trade_count","order_flow")
    signed = 2*buyq-q
    imbalance = signed/q.where(q>0)
    add("buy_fraction",bar_sum(buy,60)/bar_sum(v,60),"sum(taker buy base,60)/sum(base volume,60)","60m","volume,taker_buy_volume","order_flow")
    add("signed_quote_flow",np.arcsinh(bar_sum(signed,30)/1e6),"asinh(sum(2*taker buy quote - quote volume,30)/1e6)","30m","quote_volume,taker_buy_quote_volume","order_flow")
    add("imbalance_recent",bar_mean(imbalance,15)-bar_mean(imbalance,120),"mean(imbalance,15)-mean(imbalance,120)","120m","quote_volume,taker_buy_quote_volume","order_flow")
    add("imbalance_persistence",imbalance.rolling(120).corr(imbalance.shift()),"corr(imbalance,lag(imbalance),120)","121m","quote_volume,taker_buy_quote_volume","order_flow")
    add("flow_return_corr",r.rolling(120).corr(imbalance),"corr(r,imbalance,120)","120m","close,quote_volume,taker_buy_quote_volume","order_flow")
    add("return_gap",bar_mean((o/c.shift()-1).abs(),120),"mean(abs(open/prev_close-1),120)","121m","open,close","microstructure")
    add("trade_size_change",(bar_sum(q,30)/bar_sum(n,30))/(bar_sum(q,240)/bar_sum(n,240))-1,
        "(q30/n30)/(q240/n240)-1","240m","quote_volume,trade_count","liquidity")
    add("volume_up_down",bar_sum(q*np.sign(r),120)/bar_sum(q,120),"sum(quote volume*sign(r),120)/sum(quote volume,120)","120m","quote_volume,close","liquidity")
    if events:
        idx = pd.to_datetime([e.event_time_ns for e in events], utc=True, unit="ns")
        rate = pd.Series([e.funding_rate for e in events], index=idx)
        # At the precise settlement timestamp availability is conservatively unknown.
        lookup = clock - pd.Timedelta(nanoseconds=1)
        for name, series, formula in (("funding_last",rate,"latest strictly prior settled rate"),
                                      ("funding_delta",rate.diff(),"latest minus previous settled rate")):
            available = series.reindex(lookup, method="ffill", tolerance=pd.Timedelta(hours=24))
            available.index = clock
            add(name,available,formula,"24h","settled funding rate","derivatives")
    return pd.DataFrame(values,index=clock), registry


def daily_core(bar):
    """Qlib default K-bar, price and ROC/MA/STD/MAX/MIN formulas on UTC daily data."""
    daily=bar.resample("1D", closed="right",label="right").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum","quote_volume":"sum"})
    complete = bar.close.resample("1D", closed="right", label="right").count() == 1440
    daily = daily.where(complete)
    o,h,l,c,v,q=(daily[col] for col in ("open","high","low","close","volume","quote_volume"))
    upper=h-pd.concat([o,c],axis=1).max(axis=1)
    lower=pd.concat([o,c],axis=1).min(axis=1)-l
    eps=1e-12
    rng=h-l
    midpoint=2*c-h-l
    result={"KMID":(c-o)/o,"KLEN":rng/o,"KMID2":(c-o)/(rng+eps),
            "KUP":upper/o,"KUP2":upper/(rng+eps),"KLOW":lower/o,"KLOW2":lower/(rng+eps),
            "KSFT":midpoint/o,"KSFT2":midpoint/(rng+eps),
            "OPEN0":o/c,"HIGH0":h/c,"LOW0":l/c,"VWAP0":q/v/c}
    for w in (5,10,20,30,60):
        result.update({f"ROC{w}":c.shift(w)/c, f"MA{w}":c.rolling(w).mean()/c,
                       f"STD{w}":c.rolling(w).std(ddof=1)/c,
                       f"MAX{w}":h.rolling(w).max()/c, f"MIN{w}":l.rolling(w).min()/c})
    return pd.DataFrame(result,index=daily.index)


def add_cross_section(panel, registry):
    """Native cross-sectional transforms from eligible features only."""
    def add(name, value, formula, window, fields):
        panel[name] = value.replace([np.inf,-np.inf],np.nan)
        registry.append(dict(factor_name=name, formula=formula, lookback=window,input_fields=fields,
                              source="Crypto V1", availability_time="prediction_time",
                              category="cross_section", implementation_version="v1"))
    by_time = panel.groupby("prediction_time")
    add("cs_momentum_rank",by_time.ret_240m.rank(pct=True),"percentile rank(R240)","240m","ret_240m")
    add("cs_relative_return",panel.ret_60m-by_time.ret_60m.transform("mean"),"R60-mean_assets(R60)","60m","ret_60m")
    add("cs_relative_vol",panel.rv_120m/by_time.rv_120m.transform("median"),"RV120/median_assets(RV120)","120m","rv_120m")
    add("cs_liquidity_rank",by_time.log_quote_volume.rank(pct=True),"percentile rank(log_quote_volume)","60m","log_quote_volume")
    returns=panel.pivot(index="prediction_time",columns="symbol",values="ret_30m")
    btc=returns["BTCUSDT"]
    beta=returns.rolling(48,min_periods=36).cov(btc).div(btc.rolling(48,min_periods=36).var(),axis=0)
    keys=pd.MultiIndex.from_frame(panel[["prediction_time","symbol"]])
    coef=beta.stack().reindex(keys).to_numpy()
    add("btc_beta",pd.Series(coef,index=panel.index),"cov(R30,BTC_R30,48)/var(BTC_R30,48)","24h","ret_30m")
    resid=returns.sub(beta.mul(btc,axis=0))
    add("btc_residual",pd.Series(resid.stack().reindex(keys).to_numpy(),index=panel.index),"R30-beta*BTC_R30","24h","ret_30m,btc_beta")
    return panel
