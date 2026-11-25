"""Evaluate the sealed annual model on a separate official-archive window."""
import json

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from model_research.common import ROOT,custody,settings
from v3.acquire import bar_partition,dates

from data_engine.events import FundingRateEvent
from data.market import read_bars
from foundation.data import MarketSlice,NautilusMarket
from foundation.dataset import build
from foundation.registry import Registry
from foundation.portfolio import replay
from factor_research.statistics import block_interval
from model_research.model import Model
from model_research.study import panel,cpu,clock
from strategy_framework.execution.backtest_report import write_artifact_manifest
from training.run import code_version
from v3.common import digest,utc,write_json


class ReservedMarket(NautilusMarket):
    def __init__(self,cfg,out):
        super().__init__(cfg,ROOT)
        self.extra=out/"supplemental_market_data"
        self.settlements=out/"supplemental_funding"

    def read(self,symbol,start,end,formal_start):
        bars,coverage,inventory=read_bars(self.primary,symbol,start,end,formal_start,self.extra)
        day=(bars.index-pd.Timedelta(minutes=1)).floor("D")
        if not bars.close.groupby(day).count().eq(1440).all():
            raise ValueError(f"{symbol}: price validation did not confirm every complete real minute")
        records=json.loads((self.settlements/f"{symbol}.json").read_text())
        funds=[FundingRateEvent(int(r["fundingTime"])*1_000_000,symbol,float(r["fundingRate"]),
                                mark_price=float(r["markPrice"]),source="binance_official_funding_endpoint")
               for r in records]
        return MarketSlice(symbol,bars,funds,coverage,inventory)


def finalize(cfg,base,factor,out):
    names,sources=custody(base,factor)
    lock_file=out/"fresh_frozen_lock.json"
    lock=json.loads(lock_file.read_text())
    path=out/lock["checkpoint"]
    assert lock["source"]==sources
    assert lock["configuration_sha256"]==digest(ROOT/"configs/model_research_v1.json")
    assert lock["annual_training_sha256"]==digest(out/"annual_training.csv")
    assert all(digest(path/key)==h for key,h in lock["model_files"].items())
    assert lock["factor_sets"]==names
    verified=out/"acquisition_receipt.json"
    data_report=json.loads(verified.read_text())
    days=data_report["chosen_days"]
    begin=clock(days[0]) if days else None
    end=clock(days[-1])+pd.Timedelta(days=1) if days else None
    bars={}
    if days and data_report["status"]=="READY":
        primary=(ROOT/cfg["data_root"]).resolve()
        extra=out/"supplemental_market_data"
        for symbol in cfg["symbols"]:
            for day in dates(cfg["fresh_partition_start"],end.date().isoformat()):
                for source,root in (("primary",primary),("supplemental",extra)):
                    parts=sorted(bar_partition(root,symbol,day).glob("*.parquet"))
                    if parts:
                        bars.update({source+"/"+p.relative_to(root).as_posix():digest(p) for p in parts})
                        break
                else:
                    raise ValueError(f"sealed coverage lost a bar file {symbol} {day}")
    plan=dict(schema=1,bar_files=bars,locked_model=digest(lock_file),acquisition=digest(verified),data_status=data_report["status"],
              period=[str(begin),str(end)],official_funding_files={p.name:digest(p) for p in
                    sorted((out/"supplemental_funding").glob("*.json"))})
    saved=out/"fresh_evaluation_plan.json"
    if saved.exists() and json.loads(saved.read_text())!=plan:
        raise ValueError("sealed test input or model changed")
    if not saved.exists():
        write_json(saved,plan)
    print("FRESH_PLAN_SEALED",digest(saved),flush=True)
    receipt_path=out/"fresh_evaluation_receipt.json"
    result_path=out/"fresh_test_results.csv"
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        assert receipt["result_sha256"]==digest(result_path) and receipt["plan_sha256"]==digest(saved)
        details=pd.read_csv(result_path).iloc[0].to_dict()
    else:
        details=dict(split="FRESH_TEST",status="DATA_BLOCKED",start=begin,end_exclusive=end,
                     common_days=data_report["new_common_days"],historical_test=False,
                     evidence="metadata only",reason="Insufficient continuous nine-asset minute/funding coverage")
        if plan["data_status"]=="READY":
            assert len(days)>=lock["minimum_complete_days"] and begin>=clock(lock["bounds"][0]) and end<=clock(lock["bounds"][1])
            cfg_test={**cfg,"start":begin.date().isoformat(),"end":end.date().isoformat(),
                      "warmup_days":(begin-clock(cfg["fresh_partition_start"])).days,
                      "evaluation_horizons_minutes":[60]}
            try:
                raw,_,fund,proxy,*_=build(ReservedMarket(cfg,out),cfg_test,Registry())
            except ValueError as exc:
                details["reason"]=str(exc)
            else:
                p=panel(raw,names["FULL_88"],60,cfg)
                view=p.view(begin,end,mature=False)
                m=Model.load(path,cfg)
                pred=m.predict(p,view)
                scores,series=m.evaluate(pred,view)
                lower,upper=block_interval(view["times"],series[["ic"]].to_numpy(),cfg["block_days"],
                                           cfg["bootstrap_draws"],m.trial.seed)[0]
                stats,ledger,_,export=replay(pred,p,view,cfg,fund,proxy,m.trial.key)
                if stats["funding_mark_proxy_count"]!=0:
                    raise ValueError("fresh ledger used an unapproved funding proxy")
                ledger.reset_index(drop=True).to_parquet(out/"fresh_ledger.parquet",index=False)
                export.to_parquet(out/"fresh_targets.parquet",index=False)
                series.to_csv(out/"fresh_ic_by_section.csv",index=False)
                series.assign(month=series.prediction_time.dt.strftime("%Y-%m")).groupby("month").agg(
                    ic=("ic","mean"),rank_ic=("rank_ic","mean"),processed_ic=("processed_ic","mean"),
                    sections=("ic","count")).to_csv(out/"fresh_monthly_ic.csv")
                details=dict(split="FRESH_TEST",status="EVALUATED_FROZEN",start=begin,end_exclusive=end,
                             trial=m.trial.key,common_days=len(days),historical_test=False,evidence="BAR_EXECUTION_PROXY",
                             ic_ci_low=lower,ic_ci_high=upper,**scores,**stats)
        pd.DataFrame([details]).to_csv(result_path,index=False)
        write_json(receipt_path,dict(plan_sha256=digest(saved),result_sha256=digest(result_path),utc=utc(),
                                    evaluator_commit=code_version()))
    from model_research.report import delivery
    delivery(cfg,out,details)
    assert custody(base,factor)[1]==sources
    write_artifact_manifest(out,"model_research_v1")
    print("MODEL_RESEARCH_COMPLETE",details["status"],flush=True)


if __name__=="__main__":
    cpu()
    with threadpool_limits(limits=2):
        finalize(*settings())
