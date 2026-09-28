#!/usr/bin/env python3
"""Deterministically replay one bounded paper phase from persisted events."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.events import BarEvent, FundingRateEvent, QuoteEvent, TradeEvent  # noqa: E402
from strategy_framework.paper_trading.orchestrator import PaperOrchestrator  # noqa: E402


EVENT_TYPES = {"TradeEvent": TradeEvent, "QuoteEvent": QuoteEvent, "FundingRateEvent": FundingRateEvent}


def load_event(row: dict):
    payload = dict(row["payload"])
    cls = EVENT_TYPES[payload.pop("event_class")]
    payload.pop("event_type", None)
    return cls(**payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--phase-root", type=Path)
    args = parser.parse_args()
    repo, experiment = args.repo.resolve(), args.experiment.resolve()
    live_root = (args.phase_root or experiment).resolve()
    replay_root = live_root / "replay"
    if replay_root.exists(): shutil.rmtree(replay_root)
    replay_root.mkdir(parents=True)
    manifest_path = experiment / "manifest/paper_candidate_manifest_9symbols.csv"
    manifest = pd.read_csv(manifest_path)
    live_cases = pd.read_csv(live_root / "strategy_case_summary.csv")
    manifest = manifest[manifest.experiment_candidate_id.isin(live_cases.experiment_candidate_id)].copy()
    exchange_info = json.loads((experiment / "manifest/instrument_metadata/binance_usdm_exchange_info.json").read_text())
    warmup = {}
    for path in (live_root / "warmup").glob("*.jsonl"):
        symbol, timeframe = path.stem.rsplit("_", 1)
        warmup[(symbol, timeframe)] = [
            BarEvent(**{k: v for k, v in json.loads(line).items() if k != "event_type"})
            for line in path.read_text().splitlines() if line
        ]
    config = yaml.safe_load((experiment / "manifest/paper_trading_v1.resolved.yaml").read_text())
    orchestrator = PaperOrchestrator(
        repo=repo, experiment=replay_root, manifest=manifest, exchange_info=exchange_info,
        warmup_by_symbol_timeframe=warmup,
        initial_capital=float(config["account"]["initial_capital"]),
        target_notional=float(config["account"]["target_notional"]),
        fee_rate=float(config["fees"]["maker_rate"]), record_market_data=False,
    )
    replayed_events = 0
    for symbol_dir in sorted((live_root / "market_data").glob("symbol=*")):
        for path in sorted(symbol_dir.rglob("events.jsonl")):
            # Stream rather than materializing a full day of quotes/trades in
            # RAM.  A worker records exactly one symbol in authoritative
            # append order, and date partitions are lexically chronological.
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        orchestrator.on_event(load_event(json.loads(line)))
                        replayed_events += 1
    live_summary = pd.read_csv(live_root / "experiment_summary.csv").iloc[0]
    orchestrator.flush(int(live_summary.ended_ns))
    orchestrator.write_outputs(int(live_summary.started_ns), int(live_summary.ended_ns), "replay")
    replay_cases = pd.read_csv(replay_root / "strategy_case_summary.csv")
    keys = ["experiment_candidate_id"]
    joined = live_cases.merge(replay_cases, on=keys, suffixes=("_live", "_replay"), validate="one_to_one")
    mismatches = []
    for column in live_cases.columns:
        if column in keys: continue
        a, b = joined[f"{column}_live"], joined[f"{column}_replay"]
        if pd.api.types.is_numeric_dtype(a):
            bad = ~(np.isclose(a.astype(float), b.astype(float), rtol=1e-12, atol=1e-12, equal_nan=True))
        else:
            bad = a.fillna("").astype(str) != b.fillna("").astype(str)
        for candidate in joined.loc[bad, "experiment_candidate_id"]:
            mismatches.append({"experiment_candidate_id": candidate, "field": column})
    pd.DataFrame(mismatches, columns=["experiment_candidate_id", "field"]).to_csv(
        live_root / "replay_validation.csv", index=False
    )
    result = {
        "status": "PASSED" if not mismatches else "BLOCKED",
        "mismatch_count": len(mismatches),
        "replayed_events": replayed_events,
    }
    (replay_root / "replay_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if not mismatches else 2


if __name__ == "__main__":
    raise SystemExit(main())
