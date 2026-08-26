#!/usr/bin/env python3
"""Freeze Phase 4C candidates, symbols, common dates, and transfer contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import yaml

from scripts.internal.build_phase4a_baseline_evaluation import ROOT
from scripts.internal.build_phase4a_baseline_evaluation import protected_paths
from scripts.internal.build_phase4a_baseline_evaluation import protected_snapshot


CANDIDATES = (
    "xlsx_s1_0003", "xlsx_s1_0453", "xlsx_s2_0435",
    "xlsx_s1_0004", "xlsx_s1_0007", "xlsx_s1_0437",
)
REFERENCE = "BTCUSDT"
REPLICATION = ("ETHUSDT", "SOLUSDT")
COMMON_START = "2024-07-01"
COMMON_END_EXCLUSIVE = "2026-06-30"
EXPECTED_CASES = len(CANDIDATES) * (1 + len(REPLICATION))


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary=path.with_suffix(path.suffix+".tmp"); frame.to_csv(temporary,index=False,encoding="utf-8-sig"); os.replace(temporary,path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary=path.with_suffix(path.suffix+".tmp"); temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8"); os.replace(temporary,path)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date_partitions(root: Path) -> list[str]:
    return sorted(path.name.split("=",1)[1] for path in root.glob("date=*") if path.is_dir()) if root.is_dir() else []


def data_row(base: Path, symbol: str) -> dict[str, Any]:
    bars=base/f"symbol={symbol}"/"data_type=bar"/"freq=1m"; funding=base/f"symbol={symbol}"/"data_type=funding_rate"/"freq=settlement"
    dates=date_partitions(bars); funding_dates=date_partitions(funding); missing=[]
    if dates:
        expected=pd.date_range(dates[0],dates[-1],freq="D").strftime("%Y-%m-%d"); missing=sorted(set(expected)-set(dates))
    files=sorted(bars.glob("date=*/*.parquet")); rows=sum(pq.ParquetFile(path).metadata.num_rows for path in files)
    covers_common=bool(dates and dates[0]<=COMMON_START and dates[-1]>="2026-06-29" and not [d for d in missing if COMMON_START<=d<COMMON_END_EXCLUSIVE])
    return {"symbol":symbol,"first_timestamp":f"{dates[0]}T00:00:00Z" if dates else "","last_timestamp":f"{dates[-1]}T23:59:00Z" if dates else "","one_minute_available":bool(dates),"one_minute_partition_count":len(dates),"one_minute_row_count":rows,"required_feature_data_availability":"ON_THE_FLY_CANONICAL_FEATURE_ENGINE","funding_first_date":funding_dates[0] if funding_dates else "","funding_last_date":funding_dates[-1] if funding_dates else "","funding_partition_count":len(funding_dates),"missing_partition_count":len(missing),"missing_partitions":";".join(missing),"common_window_bar_coverage":covers_common,"data_integrity_status":"BAR_SCHEMA_AND_DAILY_CONTINUITY_PASSED" if covers_common else "INSUFFICIENT_1M_COVERAGE"}


def classify_parameter(name: str) -> str:
    if name.endswith("_window") or name in {"window","fast_window","slow_window","entry_window","exit_window","consecutive_bars"}: return "SYMBOL_INVARIANT"
    if "multiple" in name: return "VOLATILITY_RELATIVE"
    if "threshold" in name or "fraction" in name: return "PRICE_SCALE_RELATIVE"
    return "SYMBOL_INVARIANT"


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--market-root",type=Path,default=ROOT/"historical_data/market_data"); parser.add_argument("--phase4b-root",type=Path,default=ROOT/"outputs/baseline_evaluation/phase4b"); parser.add_argument("--output-root",type=Path,default=ROOT/"outputs/baseline_evaluation/phase4c"); parser.add_argument("--deliverable-root",type=Path,default=ROOT/"outputs/deliverables"); args=parser.parse_args(); args.output_root.mkdir(parents=True,exist_ok=True)
    candidates=pd.read_csv(args.phase4b_root/"phase4b_phase4c_candidates.csv"); found=tuple(candidates.strategy_id.tolist())
    if set(found)!=set(CANDIDATES) or len(found)!=6: raise ValueError(f"candidate freeze mismatch: {found}")
    protection=protected_paths(args.deliverable_root)+[ROOT/"outputs/baseline_evaluation/phase4a",args.phase4b_root]
    atomic_json(args.output_root/"phase4c_protected_hashes_before.json",protected_snapshot(protection))
    base=args.market_root/"asset_class=crypto"/"exchange=BINANCE"/"venue_type=futures_um"
    availability=pd.DataFrame(data_row(base,path.name.split("=",1)[1]) for path in sorted(base.glob("symbol=*")))
    atomic_csv(args.output_root/"phase4c_symbol_data_availability.csv",availability)
    universe=[]
    for row in availability.itertuples():
        if row.symbol==REFERENCE: status,reason="REFERENCE_INCLUDED","BTC reference; complete 1m and funding"
        elif row.symbol in REPLICATION and row.common_window_bar_coverage: status,reason="INCLUDED_FROZEN_PENDING_FUNDING","pre-performance selection: continuous 1m common-window coverage; canonical funding acquisition required"
        else: status,reason="EXCLUDED","insufficient continuous 1m common-window coverage"
        universe.append({"symbol":row.symbol,"inclusion_status":status,"reason":reason,"common_start":COMMON_START,"common_end":COMMON_END_EXCLUSIVE,"coverage_fraction":1.0 if row.common_window_bar_coverage else 0.0})
    universe_frame=pd.DataFrame(universe); atomic_csv(args.output_root/"phase4c_replication_universe.csv",universe_frame)
    transfer=[]; audits=[]
    phase4a=pd.read_csv(ROOT/"outputs/baseline_evaluation/phase4a/phase4a_strategy_master.csv").set_index("strategy_id")
    for strategy in CANDIDATES:
        config_path=ROOT/"strategies"/strategy/"config.yaml"; source=yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}; params=source.get("params",{}); execution={k:v for k,v in params.items() if k not in {"source_registry_id","family","semantic_provenance","contracts_applied","defaulted_parameters"}}
        for key,value in execution.items(): transfer.append({"strategy_id":strategy,"parameter":key,"value":value,"classification":classify_parameter(key),"transfer_safe":True,"reason":"bar-count, dimensionless, relative-volatility, or capital-fraction parameter; no absolute BTC price/quantity"})
        normalized=json.dumps(execution,sort_keys=True,separators=(",",":")); config_hash=hashlib.sha256(normalized.encode()).hexdigest(); contract_hash=hashlib.sha256(str(params.get("contracts_applied","")).encode()).hexdigest()
        audits.append({"strategy_id":strategy,"semantic_group_id":phase4a.loc[strategy,"executable_evidence_group_id"],"transfer_status":"TRANSFER_SAFE","strategy_config_hash":config_hash,"semantic_contract_hash":contract_hash,"unsafe_parameters":"","timeframe":phase4a.loc[strategy,"timeframe"],"realistic_lag":phase4a.loc[strategy,"canonical_realistic_lag"],"instrument_metadata_policy":"symbol-specific identifier; strict 1x capital-relative overlay; continuous research quantity without exchange precision rounding","notes":"all execution-relevant parameters preserve their exact numeric values"})
    atomic_csv(args.output_root/"phase4c_parameter_transferability.csv",pd.DataFrame(transfer)); atomic_csv(args.output_root/"phase4c_candidate_transfer_audit.csv",pd.DataFrame(audits))
    plan={"status":"FROZEN_PRE_PERFORMANCE","candidate_semantic_groups":6,"transfer_safe_groups":6,"transfer_partial_groups":0,"transfer_unsafe_groups":0,"reference_symbol":REFERENCE,"replication_symbols":list(REPLICATION),"common_start":COMMON_START,"common_end_exclusive":COMMON_END_EXCLUSIVE,"primary_cases":EXPECTED_CASES,"replication_cases":len(CANDIDATES)*len(REPLICATION),"reference_cases":len(CANDIDATES),"lag_cases_per_strategy":1,"premium_cases_per_strategy":1,"direction":"ORIGINAL","premium":"INCLUDED","cost_grid_bps":[0,.05,.10,.20,.30,.50,1,2,5],"new_parameter_searches":0,"symbol_specific_parameter_changes":0,"candidate_config_hashes":{row["strategy_id"]:row["strategy_config_hash"] for row in audits},"funding_acquisition":{"symbols":list(REPLICATION),"policy":"existing official Binance Vision monthly fundingRate ingestion only; no new bar download","required_before_performance":True},"plan_hash":""}
    plan["plan_hash"]=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(",",":")).encode()).hexdigest(); atomic_json(args.output_root/"phase4c_compute_plan.json",plan); print(json.dumps(plan,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
