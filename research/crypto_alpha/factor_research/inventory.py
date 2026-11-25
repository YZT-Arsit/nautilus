"""Catalogue the exact frozen implementation and check real minute causality."""
from dataclasses import replace

import numpy as np
import pandas as pd

from foundation.data import NautilusMarket
from foundation.registry import Registry
from v3.common import ROOT

GROUP = {"momentum":"Momentum","reversal":"Reversal","volatility":"Volatility",
         "liquidity":"Liquidity","order_flow":"Order Flow","flow":"Order Flow",
         "cross_section":"Cross-sectional","derivatives":"Derivatives","funding":"Derivatives",
         "microstructure":"Volatility"}
VOLUME = {"log_base_volume","log_quote_volume","relative_qvol","qvol_change","volume_up_down",
          "v2_volume_concentration","v2_turnover_volatility","v2_taker_quote_shock"}
NOTES = {
    "v2_turnover_volatility":"Std of 60-minute lag difference of log1p hourly quote volume; inclusive support 239 bars.",
    "v2_flow_decay":"60 weighted minutes with weights 1 through 60 divided by 1830.",
    "v2_flow_price_divergence":"Mean imbalance minus 0.25 times the sign of 60-minute cumulative log return.",
    "btc_beta":"48 half-hour samples; minimum 36; covariance and variance sample ddof cancel.",
    "btc_residual":"R30 minus simultaneous causal BTC beta times BTC R30; beta uses completed observations through t.",
    "v2_close_in_range":"60-minute window with minimum 50 valid observations.",
    "price_z":"120-minute population standard deviation (ddof=0).",
    "v2_funding_shock":"Last settlement minus previous rolling mean, last observation tolerance 16h.",
}
FORMULAS = {
    "v2_flow_decay":"sum_{j=1..60}(j * imbalance[t-60+j])/1830",
    "v2_flow_surprise":"(imbalance[t]-mean_120(imbalance))/std_120(imbalance,ddof=1)",
    "v2_flow_persistence":"mean_120(sign(imbalance))",
    "v2_flow_price_divergence":"mean_60(imbalance)-0.25*sign(sum_60(log(C[t]/C[t-1])))",
    "v2_flow_dispersion":"std_120(imbalance,ddof=1)",
    "v2_gk_vol":"sqrt(max(0,sum_120(0.5*ln(H/L)^2-(2*ln(2)-1)*ln(C/O)^2)))",
    "v2_bipower_jump":"1-(pi/2)*sum_120(|r[t]||r[t-1]|)/sum_120(r^2), r=ln(C/Cprev)",
    "v2_range_per_notional":"mean_60(1e6*ln(H/L)/quote_volume)",
    "v2_close_in_range":"mean_60,min50((2*C-H-L)/(H-L))",
    "v2_turnover_volatility":"std_120(diff_60(ln(1+sum_60(quote_volume))),ddof=1)",
    "v2_funding_average":"mean of last 3 settled e_j; min 2; event_time < t; staleness <=16h",
    "v2_funding_volatility":"std(ddof=1) of last 3 settled e_j; min 2; event_time < t; staleness <=16h",
    "v2_funding_shock":"e_j - mean(e_{j-1}, e_{j-2}, e_{j-3}); min 2 prior; event_time < t; staleness <=16h",
}


def inventory(source):
    reg = source.copy()
    reg["category_original"] = reg.category
    reg["category"] = reg.category.map(GROUP).fillna(reg.category)
    reg.loc[reg.factor_name.isin(VOLUME),"category"] = "Volume"
    reg["factor_id"] = reg.implementation_version+"."+reg.factor_name
    reg["mathematical_formula"] = reg.factor_name.map(FORMULAS).fillna(reg.formula)
    reg["implementation"] = reg.implementation_path
    reg["normalization"] = "raw formula output; no global preprocessing"
    reg["missing_value_policy"] = "no fabricated inputs; nonfinite -> NaN; prediction eligibility uses current completed Bar"
    reg["identical_formula_text"] = reg.duplicated(["formula","input_fields","lookback"],keep=False)
    reg["review_note"] = reg.factor_name.map(NOTES).fillna("")
    reg["lineage_status"] = "CRYPTO_ADAPTED"
    return reg


def real_causality(cfg, registry: Registry, names, out):
    market = NautilusMarket(cfg,ROOT)
    start = pd.Timestamp(cfg["start"],tz="UTC")+pd.Timedelta(days=20)
    end = start+pd.Timedelta(days=5)
    cut = start+pd.Timedelta(days=4)
    clock = pd.date_range(start,end,freq="30min",inclusive="left")
    panels = [[],[]]
    for symbol in cfg["symbols"]:
        batch = market.read(symbol,start,end,start)
        modified = batch.bars.copy()
        modified.loc[modified.index>cut] *= 1.03
        events = [replace(e,funding_rate=e.funding_rate+0.01) if e.event_time_ns>cut.value else e for e in batch.funding]
        for children,bars,settlements in zip(panels,[batch.bars,modified],[batch.funding,events]):
            f,rows = registry.symbol(bars,clock,settlements)
            children.append(f.assign(symbol=symbol,prediction_time=clock,
                                     eligible=bars.close.reindex(clock).notna().to_numpy(),
                                     feature_available_time=clock))
    full, counter = (pd.concat(x,ignore_index=True) for x in panels)
    registry.section(full,[dict(r) for r in rows])
    registry.section(counter,[dict(r) for r in rows])
    before = full.prediction_time<=cut
    assert full.loc[before,"eligible"].equals(counter.loc[before,"eligible"])
    audit = []
    for name in names:
        left,right = full.loc[before,name],counter.loc[before,name]
        np.testing.assert_allclose(left,right,equal_nan=True,rtol=1e-12,atol=1e-12,err_msg=name)
        audit.append(dict(factor_name=name,causality_check="PASS",valid_pairs=int(left.notna().sum()),
                          perturbation_start_exclusive=cut))
    result = pd.DataFrame(audit)
    result.to_csv(out/"factor_causality.csv",index=False)
    return result
