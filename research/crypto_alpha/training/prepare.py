"""Build derived feature and clock panels on the Windows data host."""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NAUTILUS = ROOT.parents[1]
sys.path[:0] = [str(ROOT), str(NAUTILUS)]

from data.market import marks_archive, read_bars, read_funding
from factors.library import add_cross_section, compute, daily_core
from labels.alignment import align, normalize


def configuration(path, override=None):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    data = (ROOT / cfg["data_root"]).resolve()
    out = Path(override).resolve() if override else (ROOT / cfg["output_root"]).resolve()
    return cfg, data, out


def prepare(cfg, data, out):
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "derived"
    cache.mkdir(exist_ok=True)
    start, end = (pd.Timestamp(cfg[k], tz="UTC") for k in ("start","end"))
    earlier = start - pd.Timedelta(days=cfg["warmup_days"])
    inputs = [NAUTILUS / p for p in cfg["funding_mark_sources"]]
    marks = marks_archive(inputs)
    coverage, inventory, all_features, all_daily, all_funding = [], [], [], [], []
    frequency_data = {i: [] for i in cfg["interval_minutes"]}
    clock = pd.date_range(start, end, freq="30min", inclusive="left")
    disable = set(cfg["disable_factors"])
    extra = (ROOT/cfg["supplemental_bar_root"]).resolve() if cfg.get("supplemental_bar_root") else None
    for symbol in cfg["symbols"]:
        print(f"prepare {symbol}", flush=True)
        bars, audit, files = read_bars(data, symbol, earlier, end, start, extra)
        events, duplicate_events = read_funding(data, symbol, earlier, end, marks)
        events_idx = pd.to_datetime([e.event_time_ns for e in events], unit="ns", utc=True)
        mark_fallback = bars.close.reindex(events_idx.floor("min")).to_numpy()
        funding_audit = pd.DataFrame([asdict(e) for e in events])
        if not funding_audit.empty:
            funding_audit["bar_mark_proxy"] = mark_fallback
            funding_audit["symbol"] = symbol
            all_funding.append(funding_audit)
        formal_funding = [e for e in events if e.event_time_ns >= start.value]
        gaps = np.diff([e.event_time_ns for e in formal_funding]) / 3.6e12
        matched = sum(e.mark_price is not None for e in formal_funding)
        audit.update(funding_events=len(formal_funding), official_marks=matched,
                     max_funding_gap_hours=float(gaps.max()) if len(gaps) else np.nan,
                     funding_duplicates=duplicate_events,
                     funding_sha256=sha256(repr(formal_funding).encode()).hexdigest())
        coverage.append(audit)
        inventory.extend((symbol, *record) for record in files)
        features, registry = compute(bars, clock, events)
        features["feature_time"] = clock.where(bars.close.reindex(clock).notna())
        features["max_feature_available_time"] = features.feature_time
        features["prediction_time"] = clock
        features["symbol"] = symbol
        all_features.append(features)
        daily = daily_core(bars)
        daily["symbol"] = symbol
        all_daily.append(daily)
        execution_open = bars.open.copy()
        execution_open.index -= pd.Timedelta(minutes=1)
        for interval in frequency_data:
            sub_clock = clock[::interval//30]
            labels = align(sub_clock, bars.close, cfg["delay_minutes"], interval)
            labels["execution_price_proxy"] = execution_open.reindex(labels.entry_time).to_numpy()
            labels["terminal_execution_price_proxy"] = execution_open.reindex(labels.label_end_time).to_numpy()
            labels["symbol"] = symbol
            frequency_data[interval].append(labels)
    cover = pd.DataFrame(coverage)
    cover.to_csv(out / "data_coverage.csv",index=False)
    pd.DataFrame(inventory,columns=["symbol","catalogue","parquet_partition","rows","file_bytes"]).to_csv(out/"data_inventory.csv",index=False)
    cover[["symbol","earliest_catalog_partition","quantity_unit"]].assign(
        start=start, end_exclusive=end, selection="ex_ante fixed requested nine; survivorship and manual selection bias",
        point_in_time_eligibility="present traded completed bar at prediction time",
        listing_and_historical_status="first archive observation only; listing/status snapshots unavailable"
    ).to_csv(out/"universe.csv", index=False)
    if (cover.valid_formal_bars / cover.expected_formal_bars < .98).any():
        raise ValueError("frozen two-year coverage below 98%; see data_coverage.csv")
    features = pd.concat(all_features,ignore_index=True).sort_values(["prediction_time","symbol"]).reset_index(drop=True)
    features = add_cross_section(features, registry)
    features = features.drop(columns=list(disable), errors="raise")
    registry = pd.DataFrame(registry)
    registry["enabled"] = ~registry.factor_name.isin(disable)
    registry.to_csv(out/"factor_registry.csv",index=False)
    pd.concat(all_daily).to_parquet(cache/"qlib_daily_core.parquet")
    pd.concat(all_funding,ignore_index=True).to_parquet(cache/"funding_events.parquet",index=False)
    for interval, sets in frequency_data.items():
        panel = features.merge(pd.concat(sets), on=["prediction_time","symbol"])
        # The prediction-time mask is independent of any later outcome.
        panel["eligible"] = panel.feature_time.notna()
        panel.loc[(~panel.eligible) | (panel.label_end_time > end), "label_raw"] = np.nan
        panel = normalize(panel, cfg["min_assets"], cfg["label_epsilon"])
        panel["raw_return"] = panel.label_raw
        panel["processed_label"] = panel.label_rank_zscore
        panel.sort_values(["prediction_time","symbol"]).to_parquet(cache/f"panel_{interval}.parquet",index=False)
        if interval == 60:
            columns = ["prediction_time","symbol","feature_time","max_feature_available_time","entry_time",
                       "label_end_time","label_available_time","label_entry_price","label_exit_price","label_raw",
                       "execution_price_proxy","eligible","label_rank","label_rank_zscore","label_raw_zscore"]
            panel[columns].to_csv(out/"label_alignment.csv",index=False)
    manifest = {"config": cfg, "funding_mark_archives": {
        str(p.relative_to(NAUTILUS)): sha256(p.read_bytes()).hexdigest() for p in inputs if p.is_file()}}
    (cache/"prepared.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print("PREPARED", flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=ROOT/"configs/v1.json")
    parser.add_argument("--output")
    args=parser.parse_args()
    prepare(*configuration(args.config,args.output))
