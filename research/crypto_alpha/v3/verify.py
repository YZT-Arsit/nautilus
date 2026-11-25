"""Verify persisted V3 forecasts, clocks, economics and source custody."""
import json

import numpy as np
import pandas as pd

from v3.common import digest,inputs,verify_prior,write_json
from v2.common import cube
from portfolio.weights import weights as rank_weights
from strategy_framework.execution.backtest_report import write_artifact_manifest


def verify():
    cfg,v1,v2,out=inputs()
    lock=json.loads((out/"fresh_frozen_lock.json").read_text())
    receipt=json.loads((out/"fresh_evaluation_receipt.json").read_text())
    assert receipt["results_sha256"]==digest(out/"fresh_test_results.csv")
    assert receipt["plan"]==digest(out/"fresh_evaluation_plan.json")
    assert verify_prior(v1,v2)==lock["frozen_results"]
    for case in lock["cases"]:
        assert [digest(p) for p in case["checkpoint"]]==case["sha256"]
    files="README.md data_coverage.csv factor_registry.csv factor_quality.csv factor_ablation.csv universe_comparison.csv loss_comparison.csv validation_results.csv model_comparison.csv portfolio_comparison.csv fresh_test_results.csv cost_sensitivity.csv summary.png".split()
    assert all((out/f).stat().st_size>0 for f in files)
    pred=pd.read_parquet(out/"fresh_predictions.parquet")
    have=pred.score.notna()
    assert (pred.loc[have,"feature_available_time"]<=pred.loc[have,"prediction_time"]).all()
    assert (pred.planned_execution_time-pred.prediction_time==pd.Timedelta(minutes=cfg["delay_minutes"])).all()
    assert (pred.label_available_time>pred.planned_execution_time).all()
    assert (pred.prediction_time>=pd.Timestamp(cfg["fresh_start"],tz="UTC")).all()
    weights=pd.read_parquet(out/"fresh_target_weights.parquet")
    assert weights.target_weight.abs().max()<=cfg["max_weight"]+1e-12
    ledger=pd.read_parquet(out/"fresh_ledger.parquet")
    groups=ledger.groupby(["model","variant"]).agg(last=("equity","last"),profit=("net_pnl","sum"),
                                                   unknown=("funding_unsettled","sum"),terminal=("gross_exposure","last"))
    assert (groups.unknown==0).all()
    assert (groups.terminal.abs()<1e-10).all()
    np.testing.assert_allclose(groups["last"]-cfg["capital"],groups.profit,atol=1e-7)
    fresh=pd.read_csv(out/"fresh_test_results.csv").set_index(["model","variant"]).sort_index()
    sens=pd.read_csv(out/"cost_sensitivity.csv")
    x=sens.loc[sens.stage.eq("fresh")&sens.bps.eq(6)&sens["mode"].eq("dynamic_rerun")].set_index(["model","variant"]).sort_index()
    np.testing.assert_allclose(fresh.net_return,x.return_net,atol=1e-11)
    older=pd.read_parquet(out/"historical_predictions.parquet",columns=["model","prediction_time","symbol","score"])
    targets=[]
    for model,group in older.groupby("model",sort=False):
        times=pd.DatetimeIndex(sorted(group.prediction_time.unique()))
        symbols=cfg["symbols"]
        scores=cube(group,times,symbols,"score")
        w=np.stack([rank_weights(s,"rank",cfg["gross"],cfg["max_weight"],cfg["min_assets"])[1] for s in scores])
        np.testing.assert_allclose(w.sum(axis=1),0,atol=1e-12)
        targets.append(pd.DataFrame(dict(prediction_time=np.repeat(times,len(symbols)),
                                        execution_time=np.repeat(times+pd.Timedelta(minutes=cfg["delay_minutes"]),len(symbols)),
                                        symbol=symbols*len(times),model=model,target_weight=w.ravel())))
    pd.concat(targets,ignore_index=True).to_parquet(out/"historical_rank_targets_all_models.parquet",index=False,compression="zstd")
    report={"required_artifacts":len(files),"historical_rank_target_models":len(targets),"fresh_accounts":len(groups),"forecast_rows":len(pred),
            "max_accounting_error_usdt":float(np.max(np.abs(groups["last"]-cfg["capital"]-groups.profit))),
            "prior_hashes":lock["frozen_results"],"orders_production":0,"orders_demo":0}
    write_json(out/"delivery_verification.json",report)
    print(json.dumps(report),flush=True)
    write_artifact_manifest(out,"crypto_alpha_v3")


if __name__=="__main__":
    verify()
