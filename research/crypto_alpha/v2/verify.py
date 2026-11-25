"""Verify Wilson-ledger reconciliation and reload compact frozen models."""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch

from v2.common import setup,cube
from v2.features import horizon_panel
from v2.networks import construct,history,predict
from training.protocol import Panel


def verify(cfg,dates,v1,out):
    torch.set_num_threads(cfg["threads"])
    summary=json.loads((out/"frozen_selection.json").read_text())
    frozen=pd.read_parquet(out/"predictions.parquet")
    base_path=out/"derived/research_panel.parquet"
    table=pd.read_parquet(base_path)
    checks={}
    for family in ("GRU","StockMixer","MASTER"):
        details=next(d for d in summary["configs"] if d["model"]==family and d["loss"]=="IC"
                     and d["interval"]==60 and d["horizon"]==60)
        key=f"60-60-{family}-IC"
        features=details["feature_names"]
        frame=horizon_panel(table,60,60,cfg["delay_minutes"],cfg["min_assets"],cfg["label_epsilon"])
        panel=Panel(frame,cfg["symbols"],features,cfg["min_assets"])
        saved=frozen.loc[frozen.model_id==key]
        dates_seen=pd.DatetimeIndex(sorted(saved.prediction_time.unique()))[::101]
        use=panel.times.get_indexer(dates_seen)
        assert (use>=0).all()
        preprocess=joblib.load(out/"models"/f"{key}.preprocessor.joblib")
        x=preprocess.transform(panel.x)
        x=np.where(panel.eligible[:,:,None],x,0.)
        histories=history(x,8)[use]
        model=construct(family,len(features),len(cfg["symbols"]),8)
        model.load_state_dict(torch.load(out/"models"/f"{key}.pt",map_location="cpu",weights_only=True))
        prediction=predict(model,histories,panel.eligible[use],"cpu")
        original=cube(saved,dates_seen,cfg["symbols"],"score")
        valid=np.isfinite(original)
        np.testing.assert_allclose(prediction[valid],original[valid],rtol=2e-5,atol=1e-6)
        checks[family]={"matched_scores":int(valid.sum()),"max_difference":float(np.max(np.abs(prediction[valid]-original[valid])))}
    for file,keys in (
        ("portfolio_ledger.parquet",["interval","model","variant"]),
        ("model_portfolio_ledger.parquet",["prediction_minutes","label_minutes","model","loss","variant"]),
    ):
        ledger=pd.read_parquet(out/file,columns=keys+["equity","net_pnl","funding_unsettled"])
        outcomes=ledger.groupby(keys).agg(equity=("equity","last"),pnl=("net_pnl","sum"),unknown=("funding_unsettled","sum"))
        assert (outcomes.unknown==0).all()
        np.testing.assert_allclose(outcomes.equity-cfg["capital"],outcomes.pnl,atol=1e-6)
        checks[file]={"reconciled_accounts":len(outcomes),"max_error_usdt":float(np.max(np.abs(outcomes.equity-cfg["capital"]-outcomes.pnl)))}
    sensitivity=pd.read_csv(out/"v2_model_cost_sensitivity.csv")
    selection=pd.read_csv(out/"model_portfolio_replay.csv")
    best=selection.loc[selection.selected_cost_winner].iloc[0]
    same=sensitivity.loc[sensitivity.bps.eq(6)&sensitivity.basis.eq("DYNAMIC_RERUN"),"net_return"].iloc[0]
    np.testing.assert_allclose(same,best.net_return,atol=1e-12)
    checks["model_count"]=int(frozen.model_id.nunique())
    checks["fees_main_dynamic_matches"]=True
    (out/"verification.json").write_text(json.dumps(checks,indent=2))
    print(json.dumps(checks),flush=True)


if __name__=="__main__":
    verify(*setup())
