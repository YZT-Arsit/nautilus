"""Validate persisted research accounts and export the existing Nautilus contract."""
import json

import numpy as np
import pandas as pd

from portfolio_research.sources import settings,verify
from portfolio.interface import validate_export
from strategy_framework.execution.backtest_report import write_artifact_manifest
from v3.common import digest,require_space,write_json


def validate():
    cfg,paths,out=settings()
    _,frozen=verify(*paths.values())
    manifest=out/"artifact_manifest.json"
    entries=json.loads(manifest.read_text())["artifacts"]
    for row in entries:
        if digest(out/row["path"])!=row["sha256"]:
            raise ValueError(f"unexpected research file change {row['path']}")
    required="README.md prediction_statistics.csv relative_strength.csv weight_comparison.csv ic_return_bridge.csv score_calibration.csv portfolio_risk.csv turnover_analysis.csv portfolio_comparison.csv cost_sensitivity.csv summary.png tests.json".split()
    assert all((out/p).stat().st_size for p in required)
    tests=json.loads((out/"tests.json").read_text())
    assert tests["failed"]==0 and tests["risk_constraints"]==tests["ic_return_reconciliation"]=="PASS"
    assert tests["source_custody"]==frozen
    models=pd.read_csv(out/"portfolio_comparison.csv")
    costs=pd.read_csv(out/"cost_sensitivity.csv")
    expected_costs=cfg["cost_grid_bps"]
    export_count=0
    max_error=0.
    clock_tolerance=pd.Timedelta(minutes=10)
    for (split,model),group in models.groupby(["split","model"]):
        require_space(out,cfg)
        root=out/"accounts"/split/model
        strength=pd.read_parquet(root/"relative_strength.parquet").reset_index()
        for row in group.itertuples():
            method=row.method
            a=root/method
            d=pd.read_parquet(a/"target_and_actual_positions.parquet")
            ledger=pd.read_parquet(a/"audited_ledger.parquet")
            m=pd.read_parquet(a/"ic_return_by_section.parquet")
            np.testing.assert_array_equal(m.execution_time,m.prediction_time+clock_tolerance)
            np.testing.assert_array_equal(m.label_end_time,m.execution_time+pd.Timedelta(hours=1))
            end=d.loc[d.terminal]
            assert end.execution_time.nunique()==1 and end.target_weight.eq(0).all()
            assert (end.actual_quantity.abs()<1e-12).all()
            pos=d.loc[~d.terminal].merge(strength,on=["prediction_time","symbol"],validate="one_to_one")
            pos["prediction_score"]=pos.raw
            pos["relative_strength"]=(pos[{"rank_l1":"rank_zscore","raw_l1":"demeaned","zscore_l1":"zscore"}.get(method,"target_weight")])
            resolved=validate_export(pos,cfg["delay_minutes"])
            resolved.to_parquet(a/"nautilus_targets.parquet",index=False,compression="zstd")
            export_count+=len(resolved)
            grouped=d.groupby("execution_time").target_weight
            np.testing.assert_allclose(grouped.sum().to_numpy(),0,atol=1e-12)
            assert (grouped.apply(lambda x:x.abs().sum())<=cfg["gross"]+1e-12).all()
            assert d.target_weight.abs().max()<=cfg["single_asset_cap"]+1e-12
            np.testing.assert_allclose(d.previous_quantity+d.filled_quantity_delta,d.actual_quantity,atol=1e-9)
            total=ledger.gross_pnl+ledger.funding-ledger.fees-ledger.spread-ledger.slippage
            error=float(total.sum()-ledger.equity.iloc[-1]+cfg["capital"])
            max_error=max(max_error,abs(error))
            assert abs(error)<1e-6 and ledger.funding_unsettled.sum()==0
            c=costs.loc[costs.split.eq(split)&costs.model.eq(model)&costs.method.eq(method)]
            assert c.groupby("mode").single_sided_cost_bps.apply(list).apply(lambda x:sorted(x)==expected_costs).all()
            v=c.loc[c.single_sided_cost_bps.eq(cfg["primary_cost_bps"]),"total_cost_return"]
            np.testing.assert_allclose(v,row.net_return,atol=1e-10)
    data=dict(previous_manifest_files_verified=len(entries),delivered_files_verified=len(required),
              validated_accounts=len(models),compatible_target_rows=export_count,max_equity_error_usdt=max_error,
              orders_production=0,orders_demo=0)
    write_json(out/"delivery_verification.json",data)
    write_artifact_manifest(out,"portfolio_research_v1")
    print(json.dumps(data),flush=True)


if __name__=="__main__":
    validate()
