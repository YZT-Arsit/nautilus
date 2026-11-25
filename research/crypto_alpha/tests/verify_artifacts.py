"""Verify the frozen experiment against saved predictions and the replay ledger."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parents[1])]

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import torch

from models.cpu import net,prediction
from portfolio.interface import validate_export
from training.prepare import configuration


def verify(out,cfg):
    torch.set_num_threads(cfg["threads"])
    panel=pd.read_parquet(out/"derived/panel_60.parquet")
    preds=pd.read_parquet(out/"predictions.parquet")
    target=pd.read_parquet(out/"nautilus_targets.parquet")
    pd.testing.assert_frame_equal(validate_export(target,cfg["delay_minutes"]),target)
    label=pd.read_csv(out/"label_alignment.csv")
    time_cols=["prediction_time","feature_time","max_feature_available_time","entry_time",
               "label_end_time","label_available_time"]
    for col in time_cols:
        label[col]=pd.to_datetime(label[col],utc=True,format="mixed")
    already=label.eligible
    assert (label.loc[already,"max_feature_available_time"]<=label.loc[already,"prediction_time"]).all()
    assert (label.label_available_time>=label.label_end_time).all()
    assert (label.entry_time-label.prediction_time==pd.Timedelta(minutes=cfg["delay_minutes"])).all()
    assert (label.label_end_time-label.entry_time==pd.Timedelta(hours=1)).all()
    labels=label.loc[label.label_raw.notna()]
    np.testing.assert_allclose(labels.label_raw,labels.label_exit_price/labels.label_entry_price-1,atol=1e-13)
    rolling=pd.read_csv(out/"annual_training.csv")
    assert (pd.to_datetime(rolling.last_label_available_time,utc=True,format="mixed")<=
            pd.to_datetime(rolling.training_cutoff,utc=True,format="mixed")).all()
    weight=pd.read_parquet(out/"target_weights.parquet")
    assert weight.target_weight.abs().max()<=cfg["max_weight"]+1e-12
    weight["abs_weight"]=weight.target_weight.abs()
    exposure=weight.groupby(["prediction_time","model_version","weight_method"]).agg(
        gross=("abs_weight","sum"),net=("target_weight","sum"))
    assert exposure.gross.max()<=cfg["gross"]+1e-12
    np.testing.assert_allclose(exposure.net,0,atol=1e-12)
    ledger=pd.read_csv(out/"portfolio_returns.csv")
    for (_,method),frame in ledger.groupby(["model","weight_method"]):
        end=frame.equity.iloc[-1]
        net_pnl=frame.eval("gross_pnl + funding - fees - spread - slippage").sum()
        np.testing.assert_allclose(end-cfg["capital"],net_pnl,atol=1e-7)
        assert np.isclose(frame.gross_exposure.iloc[-1],0,atol=1e-12), method
        assert frame.unsettled_funding.sum()==0
    sample=preds.prediction_time.drop_duplicates().sort_values().iloc[::100]
    checks={}
    for family in ("Ridge","LightGBM","MLP-MSE","MLP-IC"):
        stem=out/"models"/family
        job=json.loads(stem.with_suffix(".json").read_text())
        scaler=joblib.load(stem.with_suffix(".preprocess.joblib"))
        if family=="Ridge":
            model=joblib.load(stem.with_suffix(".joblib"))
        elif family=="LightGBM":
            model=lgb.Booster(model_file=str(stem.with_suffix(".txt")))
        else:
            model=net(len(job["features"]))
            model.load_state_dict(torch.load(stem.with_suffix(".pt"),map_location="cpu",weights_only=True))
        chosen=preds.loc[(preds.model==family)&preds.prediction_time.isin(sample)]
        idx=chosen[["prediction_time","symbol"]]
        read=idx.merge(panel,on=["prediction_time","symbol"],how="left",validate="one_to_one")
        x=scaler.transform(read[job["features"]].to_numpy())
        x=x.reshape(-1,len(cfg["symbols"]),len(job["features"]))
        value=prediction(model,family,x).ravel()
        expected=chosen.prediction_score.to_numpy()
        np.testing.assert_allclose(value[np.isfinite(expected)],expected[np.isfinite(expected)],rtol=2e-5,atol=2e-7)
        checks[family]={"verified_predictions":int(np.isfinite(expected).sum()),
                        "maximum_abs_reloaded_delta":float(np.abs(value-expected)[np.isfinite(expected)].max())}
    result={"label_alignment":"PASS","training_cutoff":"PASS","portfolio_weights":"PASS",
            "ledger_reconciliation":"PASS","model_reload":checks,"funding_unsettled":int(ledger.unsettled_funding.sum()),
            "observed_max_gross_target":float(exposure.gross.max()),
            "observed_max_abs_net_target":float(exposure.net.abs().max())}
    (out/"verification.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=ROOT/"configs/v1.json")
    parser.add_argument("--output")
    args=parser.parse_args()
    cfg,_,path=configuration(args.config,args.output)
    verify(path,cfg)
