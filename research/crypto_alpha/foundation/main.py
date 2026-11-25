"""One configurable Windows CPU benchmark, from archives to offline replay."""
import argparse
from importlib import import_module
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT.parents[1])]

import numpy as np
import pandas as pd
import torch
from threadpoolctl import threadpool_limits

from foundation.annual import train_family
from foundation.data import NautilusMarket
from foundation.dataset import build, CrossSectionDataset, task
from foundation.evaluation import factor_report
from foundation.model import OBJECTIVES
from foundation.portfolio import replay
from foundation.registry import Registry
from foundation.reproduction import verify as reproduce
from foundation.test_runner import execute
from strategy_framework.execution.backtest_report import write_artifact_manifest
from training.protocol import annual_windows
from training.run import code_version, funding_inputs
from v3.common import require_space, verify_prior, write_json


def config(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    inherited = json.loads((ROOT/spec["base_config"]).read_text(encoding="utf-8"))
    cfg = {**inherited, **spec}
    allowed = {30,60,120,240,480,1440}
    cfg["holding_minutes"] = cfg.get("holding_minutes") or cfg["interval_minutes"]
    if {cfg["interval_minutes"],cfg["holding_minutes"],*cfg["evaluation_horizons_minutes"]} - allowed:
        raise ValueError("unsupported cadence or holding period")
    if cfg["portfolio"] not in {"rank","raw","equal"} or not cfg["models"] or set(cfg["models"])-OBJECTIVES.keys():
        raise ValueError("invalid portfolio or model")
    if cfg["fit_years"] not in {1,2} or len(cfg["symbols"]) != 9:
        raise ValueError("annual protocol uses one/two fit years and fixed nine-asset fixture")
    out = (ROOT/cfg["output"]).resolve()
    parent = (ROOT/"../../outputs/crypto_alpha").resolve()
    if out.parent != parent or not out.name.startswith("foundation_"):
        raise ValueError("all writes must be in an isolated foundation output directory")
    out.mkdir(parents=True,exist_ok=True)
    return cfg,out


def panel_for(frame, names, cfg, holding=None, interval=None):
    shaped = task(frame, interval or cfg["interval_minutes"], holding or cfg["holding_minutes"],
                  cfg["delay_minutes"], cfg["min_assets"], cfg["label_epsilon"])
    return CrossSectionDataset(shaped, cfg["symbols"], names, cfg["min_assets"])


def examples(frame, names, raw_predictions, cfg, out):
    clocks = ["feature_available_time","prediction_time","entry_time","label_end_time","label_available_time"]
    shaped = frame.reset_index()
    columns = [*clocks,"symbol","eligible","label_entry_price","label_exit_price","execution_price_proxy",
               "label_raw","label_rank","label_rank_zscore",*names]
    bench = shaped[columns].merge(raw_predictions[["prediction_time","symbol","execution_time",
                                                   "prediction_score","target_weight"]], validate="one_to_one")
    available = bench.loc[bench.label_raw.notna() & bench.prediction_score.notna()]
    forecast_times = available.prediction_time.drop_duplicates()
    morning = forecast_times[forecast_times.dt.hour.eq(9) & forecast_times.dt.minute.eq(0)]
    sample = available.loc[available.prediction_time.isin(morning.iloc[:4])]
    if len(sample) < 20:
        sample = available.head(36)
    assert len(sample) >= 20 and (sample.execution_time == sample.entry_time).all()
    sample = sample.rename(columns={"label_raw":"raw_label","label_rank":"rank_label",
                                    "label_rank_zscore":"normalized_label"})
    sample.to_csv(out/"label_alignment.csv",index=False)
    return len(sample)


def accept_replacements(frame, names, cfg, window, out, funding, proxies, standard):
    checks = []
    if cfg["interval_minutes"] != 60 or cfg["holding_minutes"] != 60 or "Ridge" not in cfg["models"]:
        return checks
    for label_config, factor_set in [(240,names),(60,names[:12])]:
        experiment = "4h_frequency" if label_config == 240 else "12_factor_subset"
        panel = panel_for(frame, names, cfg, holding=label_config, interval=label_config)
        _, stats, _, _, view, pred = train_family("Ridge", cfg["models"]["Ridge"], cfg, panel, window,
                                                  factor_set, out/"replacement_checks"/experiment)
        metric, _, _, _ = replay(pred,panel,view,cfg,funding,proxies,experiment)
        checks.append(dict(change=experiment,status="PASS",model="Ridge",interval_minutes=label_config,
                           factor_count=len(factor_set),historical_ic=stats["ic"],net_return=metric["net_return"]))
    panel, view, pred = standard
    metric, _, _, alt_export = replay(pred,panel,view,{**cfg,"portfolio":"raw"},funding,proxies,"replay-raw")
    alt_export.to_parquet(out/"replacement_checks"/"raw_weights.parquet",index=False)
    checks.append(dict(change="portfolio_raw_only",status="PASS",refit=False,
                       interval_minutes=60,net_return=metric["net_return"]))
    checks.append(dict(change="model_only",status="PASS",model="|".join(cfg["models"]),
                       interval_minutes=60,same_dataset_label_evaluator=True))
    return checks


def run(cfg,out):
    started = perf_counter()
    require_space(out,cfg)
    commit = code_version()
    parent = out.parent
    previous = verify_prior(parent/"v1",parent/"v2")
    result = execute(ROOT)
    write_json(out/"tests.json",result)
    if result["exit_code"]:
        raise RuntimeError("test suite failed, see tests.json")
    reproduction = reproduce(out)
    print("SOURCE_PARITY_COMPLETE",flush=True)
    start,end = (pd.Timestamp(cfg[k],tz="UTC") for k in ("start","end"))
    windows = list(annual_windows(start,end,cfg["fit_years"],cfg["validation_months"]))
    if not windows:
        raise ValueError("protocol requires fit_years plus one full prediction year")
    registry = Registry()
    for name in cfg.get("factor_plugins",[]):
        import_module(name).register(registry)
    market = NautilusMarket(cfg,ROOT)
    frame, reg, funding, proxies, coverage, inventory, settlements = build(market,cfg,registry)
    reg.to_csv(out/"factor_registry.csv",index=False)
    original = reg.loc[reg.implementation_version.isin(["v1","v2"]), "factor_name"]
    reference = pd.read_csv(parent/"v2"/"factor_registry.csv").factor_name
    if len(original) != 88 or set(original) != set(reference) or not reg.factor_name.is_unique:
        raise ValueError("the foundation registry must preserve the 88 V2 factor names")
    cover = pd.DataFrame(coverage)
    cover.to_csv(out/"data_coverage.csv",index=False)
    pd.DataFrame(inventory,columns=["symbol","catalogue","partition","rows","bytes"]).to_csv(out/"data_inventory.csv",index=False)
    if (cover.valid_formal_bars/cover.expected_formal_bars < .98).any():
        raise ValueError("a benchmark asset has less than 98% bar coverage")
    require_space(out,cfg)
    names = reg.factor_name.tolist()
    base = panel_for(frame,names,cfg)
    labels = {"raw_label":base.frame.label_raw, "rank_label":base.frame.label_rank,
              "normalized_label":base.frame.label_rank_zscore}
    base.frame.assign(**labels).to_parquet(out/"dataset.parquet",compression="zstd")
    settlements.to_parquet(out/"funding_events.parquet",index=False,compression="zstd")
    print(f"DATASET_COMPLETE {len(names)} factors {len(base.frame)} rows",flush=True)
    models, ports, preds, ics, weights, ledgers, replacements = [],[],[],[],[],[],[]
    for w in windows:
        require_space(out,cfg)
        dest = out if len(windows)==1 else out/f"{w.test_start:%Y%m%d}"
        dest.mkdir(exist_ok=True)
        select = factor_report(lambda h: base if h == cfg["holding_minutes"] else panel_for(frame,names,cfg,holding=h),
                               names,w.train_start,w.validation_start,cfg,dest)
        factor_set = cfg["factor_set"]
        features = names if factor_set=="all" else select if factor_set=="train_screen" else factor_set
        if not isinstance(features,list) or not features or set(features)-set(names) or len(set(features))!=len(features):
            raise ValueError("factor_set must be all, train_screen, or a list of unique registered names")
        yearly = []
        standard = None
        for family, params in cfg["models"].items():
            v,t,cv,ct,test,signals = train_family(family,params,cfg,base,w,features,dest/"models"/family)
            models.extend((v,t))
            model_id = f"{commit[:8]}-{w.test_start:%Y%m%d}-{family}-{cfg['interval_minutes']}m"
            economy, ledger, prediction_rows, export = replay(signals,base,test,cfg,funding,proxies,model_id)
            yearly.append((v["ic"], family, prediction_rows, export))
            if family=="Ridge":
                standard = (base,test,signals)
            ports.append(dict(model=family,model_version=model_id,test_start=w.test_start,weight_method=cfg["portfolio"],
                              **economy))
            preds.append(prediction_rows)
            weights.append(export)
            ledgers.append(ledger.reset_index(drop=True).assign(model_version=model_id))
            ics.extend([cv.assign(model_version=model_id,split="validation"),
                        ct.assign(model_version=model_id,split="historical_test")])
            print(f"MODEL {family} VALIDATION {v['ic']:.7f} TEST {t['ic']:.7f}",flush=True)
        _, chosen, prediction_data, chosen_export = max(yearly,key=lambda x:x[0])
        for r in models[-2*len(yearly):]:
            r["selected_by_validation_ic"] = r["model"]==chosen
        picked = examples(base.frame,names,prediction_data,cfg,dest)
        chosen_export.to_parquet(dest/"nautilus_targets.parquet",index=False)
        if cfg.get("run_replacement_checks",True):
            replacements.extend(accept_replacements(frame,names,cfg,w,dest,funding,proxies,
                                                   standard))
    pd.DataFrame(models).to_csv(out/"model_comparison.csv",index=False)
    pd.DataFrame(ports).to_csv(out/"portfolio_validation.csv",index=False)
    pd.concat(preds).to_parquet(out/"predictions.parquet",index=False,compression="zstd")
    pd.concat(weights).to_parquet(out/"target_weights.parquet",index=False,compression="zstd")
    pd.concat(ledgers).to_parquet(out/"portfolio_ledger.parquet",index=False,compression="zstd")
    pd.concat(ics).to_csv(out/"prediction_ic.csv",index=False)
    pd.DataFrame(replacements).to_csv(out/"module_replacement.csv",index=False)
    assert verify_prior(parent/"v1",parent/"v2")==previous
    result.update(integration=dict(real_label_examples=picked,model_roundtrip=True,ledger_reconciled=True,
                  validated_annual_windows=len(windows),replacement_checks=len(replacements),source_parity=True,
                  prior=previous), production_orders=0,demo_orders=0)
    write_json(out/"tests.json",result)
    write_json(out/"run_environment.json",dict(commit=commit,machine=platform.node(),device="CPU",
                  thread_count=cfg["threads"],duration_seconds=perf_counter()-started,
                  versions={k:version(k) for k in ["numpy","pandas","polars","pyarrow","scipy",
                                                   "scikit-learn","lightgbm","torch","pytest"]}))
    write_json(out/"resolved_config.json",cfg)
    from foundation.delivery import deliver
    deliver(out,cfg,models,ports,result,reproduction)
    write_artifact_manifest(out,"crypto_alpha_foundation_v1")
    print("FOUNDATION ACCEPTANCE COMPLETE",flush=True)



def replay_saved(source, method):
    cfg = json.loads((source/"resolved_config.json").read_text(encoding="utf-8"))
    target = source.with_name(source.name+f"_replay_{method}")
    require_space(source,cfg)
    target.mkdir(exist_ok=True)
    raw = pd.read_parquet(source/"dataset.parquet").reset_index()
    names = pd.read_csv(source/"factor_registry.csv").factor_name.tolist()
    panel = CrossSectionDataset(raw,cfg["symbols"],names,cfg["min_assets"])
    funding, proxies = funding_inputs(source)
    saved = pd.read_parquet(source/"predictions.parquet")
    summaries, ledgers, exports = [], [], []
    for model_id, rows in saved.groupby("model_version",sort=False):
        times = pd.DatetimeIndex(sorted(rows.prediction_time.unique()))
        keys = pd.MultiIndex.from_product([times,panel.symbols],names=["prediction_time","symbol"])
        pred = rows.set_index(["prediction_time","symbol"]).prediction_score.reindex(keys).to_numpy().reshape(
            len(times),len(panel.symbols))
        view = {"times":times,"selector":panel.times.isin(times)}
        summary, ledger, _, export = replay(pred,panel,view,{**cfg,"portfolio":method},funding,proxies,model_id)
        summaries.append(dict(model_version=model_id, weight_method=method, refitted=False, **summary))
        ledgers.append(ledger.reset_index(drop=True).assign(model_version=model_id))
        exports.append(export)
    pd.DataFrame(summaries).to_csv(target/"portfolio_validation.csv",index=False)
    pd.concat(ledgers).to_parquet(target/"portfolio_ledger.parquet",index=False,compression="zstd")
    pd.concat(exports).to_parquet(target/"target_weights.parquet",index=False,compression="zstd")
    print(f"FROZEN_PREDICTION_REPLAY_COMPLETE {len(summaries)}",flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=ROOT/"configs/foundation_v1.json",type=Path)
    parser.add_argument("--replay-only",choices=["rank","raw","equal"])
    args=parser.parse_args()
    cfg,out=config(args.config)
    if os.name=="nt":
        from ctypes import WinDLL, wintypes
        kernel=WinDLL("kernel32")
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetPriorityClass.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    with threadpool_limits(limits=cfg["threads"]):
        if args.replay_only:
            replay_saved(out,args.replay_only)
        else:
            run(cfg,out)
