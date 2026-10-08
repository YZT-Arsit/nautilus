#!/usr/bin/env python3
"""Paper strategy process consuming only a committed canonical active-active WAL."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import yaml

from data_engine.events import BarEvent, FundingRateEvent, QuoteEvent, TradeEvent
from strategy_framework.paper_trading.orchestrator import PaperOrchestrator, manifest_hash


EVENT_TYPES = {"TradeEvent": TradeEvent, "QuoteEvent": QuoteEvent, "FundingRateEvent": FundingRateEvent}


def load_event(row: dict):
    payload = dict(row["payload"])
    cls = EVENT_TYPES[payload.pop("event_class")]
    payload.pop("event_type", None)
    return cls(**payload)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--wal-root", type=Path, required=True)
    parser.add_argument("--start-ns", type=int, required=True)
    parser.add_argument("--end-ns", type=int, required=True)
    parser.add_argument("--expected-bars", type=int, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--warmup-source", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(); experiment = args.experiment.resolve()
    run_root = experiment / "workers" / "BTCUSDT"
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment / "manifest/paper_candidate_manifest_9symbols.csv"
    expected_hash = (experiment / "manifest/paper_candidate_manifest_9symbols.sha256").read_text().strip()
    if manifest_hash(manifest_path) != expected_hash:
        raise RuntimeError("candidate manifest hash mismatch")
    manifest = pd.read_csv(manifest_path)
    active = manifest.loc[manifest.experiment_candidate_id.eq(args.candidate_id)].copy()
    if len(active) != 1:
        raise RuntimeError("candidate-id must resolve to one frozen row")
    exchange_info = json.loads(
        (experiment / "manifest/instrument_metadata/binance_usdm_exchange_info.json").read_text()
    )
    warmup_path = run_root / "warmup" / "BTCUSDT_1m.jsonl"
    warmup_path.parent.mkdir(parents=True, exist_ok=True)
    warmup_path.write_bytes(args.warmup_source.read_bytes())
    warmup = [
        BarEvent(**{k: v for k, v in json.loads(line).items() if k != "event_type"})
        for line in warmup_path.read_text().splitlines() if line
    ]
    config = yaml.safe_load((experiment / "manifest/paper_trading_v1.resolved.yaml").read_text())
    orchestrator = PaperOrchestrator(
        repo=repo, experiment=run_root, manifest=active, exchange_info=exchange_info,
        warmup_by_symbol_timeframe={("BTCUSDT", "1m"): warmup},
        initial_capital=float(config["account"]["initial_capital"]),
        target_notional=float(config["account"]["target_notional"]),
        fee_rate=float(config["fees"]["maker_rate"]), record_market_data=False,
    )
    wal_path = args.wal_root / "events.jsonl"
    commit_path = args.wal_root / "commit.json"
    deadline = args.end_ns / 1e9 + 90.0
    while not wal_path.exists() and time.time() < deadline:
        time.sleep(0.1)
    if not wal_path.exists():
        raise RuntimeError("canonical WAL never appeared")
    processed_sequence = 0
    pending: list[dict] = []
    with wal_path.open(encoding="utf-8") as handle:
        while time.time() < deadline:
            line = handle.readline()
            if line:
                pending.append(json.loads(line))
            committed = 0
            if commit_path.exists():
                try:
                    committed = int(json.loads(commit_path.read_text())["committed_sequence"])
                except (json.JSONDecodeError, KeyError, OSError):
                    committed = processed_sequence
            consumed = 0
            for row in pending:
                sequence = int(row["canonical_sequence"])
                if sequence > committed:
                    break
                event = load_event(row)
                if args.start_ns <= int(event.event_time_ns) < args.end_ns:
                    orchestrator.on_event(event)
                processed_sequence = sequence
                consumed += 1
            if consumed:
                del pending[:consumed]
            if time.time_ns() >= args.end_ns and processed_sequence >= committed and not line:
                collector_validation = experiment / "active_active" / "collector_validation.json"
                if collector_validation.exists():
                    break
            if not line:
                time.sleep(0.05)

    orchestrator.flush(args.end_ns)
    summary = orchestrator.write_outputs(args.start_ns, args.end_ns, "authoritative_24h")
    orchestrator.recorder.close()
    bar_path = run_root / "bars" / "BTCUSDT_1m.jsonl"
    observed = sum(1 for line in bar_path.read_text().splitlines() if line) if bar_path.exists() else 0
    collector_validation_path = experiment / "active_active" / "collector_validation.json"
    collector = json.loads(collector_validation_path.read_text()) if collector_validation_path.exists() else {}
    canonical_missing = int(collector.get("canonical_missing_minutes", args.expected_bars))
    status = "PASSED" if observed == args.expected_bars and canonical_missing == 0 else "BLOCKED"
    wal_bytes = wal_path.stat().st_size
    pd.DataFrame([{
        "path": wal_path.relative_to(experiment).as_posix(), "bytes": wal_bytes,
        "sha256": sha256_file(wal_path),
        "rows": processed_sequence, "source": "BINANCE_USDM_ACTIVE_ACTIVE_CANONICAL",
    }]).to_csv(run_root / "data_quality_summary.csv", index=False)
    validation = {
        "status": status, "expected_bars": args.expected_bars, "observed_bars": observed,
        "canonical_missing_minutes": canonical_missing,
        "processed_canonical_sequence": processed_sequence,
        "collector": collector, "summary": summary,
        "production_market_data": "READ_ONLY",
        "production_trading_client": "NOT_INITIALIZED", "production_exchange_orders": 0,
    }
    (run_root / "dry_run_validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    return 0 if status == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
