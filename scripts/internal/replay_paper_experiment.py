#!/usr/bin/env python3
"""Deterministically replay one bounded paper phase from persisted events."""

from __future__ import annotations

import argparse
import hashlib
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

from data_engine.events import BarEvent  # noqa: E402
from data_engine.events import FundingRateEvent  # noqa: E402
from data_engine.events import QuoteEvent  # noqa: E402
from data_engine.events import TradeEvent  # noqa: E402
from data_engine.live.binance_ws import normalize_agg_trade  # noqa: E402
from strategy_framework.paper_trading.orchestrator import PaperOrchestrator  # noqa: E402


EVENT_TYPES = {"TradeEvent": TradeEvent, "QuoteEvent": QuoteEvent, "FundingRateEvent": FundingRateEvent}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compare_csv(
    live_path: Path,
    replay_path: Path,
    artifact: str,
    mismatches: list[dict[str, str]],
    candidate_ids: set[str],
) -> None:
    """Compare deterministic CSV artifacts with tight numeric tolerance."""
    if not live_path.exists() or not replay_path.exists():
        mismatches.append({
            "artifact": artifact,
            "candidate_id": "",
            "field": "FILE_EXISTS",
            "detail": f"live={live_path.exists()},replay={replay_path.exists()}",
        })
        return
    live = pd.read_csv(live_path)
    replay = pd.read_csv(replay_path)
    if "experiment_candidate_id" in live.columns:
        live = live[live.experiment_candidate_id.astype(str).isin(candidate_ids)].reset_index(drop=True)
    if "experiment_candidate_id" in replay.columns:
        replay = replay[replay.experiment_candidate_id.astype(str).isin(candidate_ids)].reset_index(drop=True)
    if list(live.columns) != list(replay.columns):
        mismatches.append({
            "artifact": artifact,
            "candidate_id": "",
            "field": "COLUMNS",
            "detail": f"live={list(live.columns)},replay={list(replay.columns)}",
        })
        return
    if len(live) != len(replay):
        mismatches.append({
            "artifact": artifact,
            "candidate_id": "",
            "field": "ROW_COUNT",
            "detail": f"live={len(live)},replay={len(replay)}",
        })
        return
    candidate_column = "experiment_candidate_id"
    for column in live.columns:
        a, b = live[column], replay[column]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            bad = ~np.isclose(
                pd.to_numeric(a, errors="coerce").to_numpy(float),
                pd.to_numeric(b, errors="coerce").to_numpy(float),
                rtol=1e-12,
                atol=1e-12,
                equal_nan=True,
            )
        else:
            bad = a.fillna("").astype(str).to_numpy() != b.fillna("").astype(str).to_numpy()
        for index in np.flatnonzero(bad):
            candidate = str(live.iloc[index][candidate_column]) if candidate_column in live.columns else ""
            mismatches.append({
                "artifact": artifact,
                "candidate_id": candidate,
                "field": column,
                "detail": f"row={index}",
            })


def _compare_bytes(
    live_path: Path,
    replay_path: Path,
    artifact: str,
    mismatches: list[dict[str, str]],
) -> None:
    if not live_path.exists() or not replay_path.exists():
        mismatches.append({
            "artifact": artifact,
            "candidate_id": "",
            "field": "FILE_EXISTS",
            "detail": f"live={live_path.exists()},replay={replay_path.exists()}",
        })
    elif _sha256(live_path) != _sha256(replay_path):
        mismatches.append({
            "artifact": artifact,
            "candidate_id": "",
            "field": "SHA256",
            "detail": f"live={_sha256(live_path)},replay={_sha256(replay_path)}",
        })


def _compare_account_state(
    live_path: Path,
    replay_path: Path,
    candidate_ids: set[str],
    mismatches: list[dict[str, str]],
) -> None:
    artifact = "positions_and_account_state"
    if not live_path.exists() or not replay_path.exists():
        mismatches.append({
            "artifact": artifact,
            "candidate_id": "",
            "field": "FILE_EXISTS",
            "detail": f"live={live_path.exists()},replay={replay_path.exists()}",
        })
        return
    live = json.loads(live_path.read_text())
    replay = json.loads(replay_path.read_text())
    for candidate in sorted(candidate_ids):
        if live.get(candidate) != replay.get(candidate):
            mismatches.append({
                "artifact": artifact,
                "candidate_id": candidate,
                "field": "ACCOUNT_STATE",
                "detail": "canonical JSON values differ",
            })
def load_event(row: dict):
    payload = dict(row["payload"])
    cls = EVENT_TYPES[payload.pop("event_class")]
    payload.pop("event_type", None)
    return cls(**payload)


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--phase-root", type=Path)
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--replay-subdir", default="replay")
    parser.add_argument("--replay-output-root", type=Path)
    parser.add_argument("--insert-aggtrades-json", type=Path)
    parser.add_argument("--diagnostic-output-only", action="store_true")
    args = parser.parse_args()
    repo, experiment = args.repo.resolve(), args.experiment.resolve()
    live_root = (args.phase_root or experiment).resolve()
    replay_root = (
        args.replay_output_root.resolve()
        if args.replay_output_root
        else live_root / args.replay_subdir
    )
    if replay_root.exists():
        shutil.rmtree(replay_root)
    replay_root.mkdir(parents=True)
    manifest_path = experiment / "manifest/paper_candidate_manifest_9symbols.csv"
    manifest = pd.read_csv(manifest_path)
    live_cases = pd.read_csv(live_root / "strategy_case_summary.csv")
    if args.candidate_id:
        live_cases = live_cases[
            live_cases.experiment_candidate_id.astype(str).isin(set(args.candidate_id))
        ].copy()
        if live_cases.empty:
            raise RuntimeError("requested replay candidate was not present in live results")
    manifest = manifest[manifest.experiment_candidate_id.isin(live_cases.experiment_candidate_id)].copy()
    candidate_ids = set(manifest.experiment_candidate_id.astype(str))
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
    inserted_events = 0
    live_summary = pd.read_csv(live_root / "experiment_summary.csv").iloc[0]
    extra_events = []
    if args.insert_aggtrades_json:
        raw_trades = json.loads(args.insert_aggtrades_json.read_text(encoding="utf-8"))
        for raw in raw_trades:
            event = normalize_agg_trade(
                {**raw, "e": "aggTrade", "s": "BTCUSDT"},
                instrument_id="BTCUSDT-PERP.BINANCE",
                receive_time_ns=int(raw["T"]) * 1_000_000,
            )
            if event is not None:
                event.source = "POST_HOC_BACKFILL_FOR_SENSITIVITY_ONLY"
                extra_events.append(event)
        extra_events.sort(key=lambda event: (int(event.event_time_ns), str(event.trade_id)))
    extra_index = 0

    def emit_extra_before(timestamp_ns: int) -> None:
        nonlocal extra_index, replayed_events, inserted_events
        while extra_index < len(extra_events) and int(extra_events[extra_index].event_time_ns) <= timestamp_ns:
            event = extra_events[extra_index]
            if int(live_summary.started_ns) <= int(event.event_time_ns) < int(live_summary.ended_ns):
                orchestrator.on_event(event)
                replayed_events += 1
                inserted_events += 1
            extra_index += 1

    canonical_wal = experiment / "active_active" / "canonical_wal" / "events.jsonl"
    if canonical_wal.exists():
        with canonical_wal.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    event = load_event(json.loads(line))
                    emit_extra_before(int(event.event_time_ns))
                    if int(live_summary.started_ns) <= int(event.event_time_ns) < int(live_summary.ended_ns):
                        orchestrator.on_event(event)
                        replayed_events += 1
    for symbol_dir in sorted((live_root / "market_data").glob("symbol=*")):
        for path in sorted(symbol_dir.rglob("events.jsonl")):
            # Stream rather than materializing a full day of quotes/trades in
            # RAM.  A worker records exactly one symbol in authoritative
            # append order, and date partitions are lexically chronological.
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        event = load_event(json.loads(line))
                        emit_extra_before(int(event.event_time_ns))
                        orchestrator.on_event(event)
                        replayed_events += 1
    emit_extra_before(int(live_summary.ended_ns))
    orchestrator.flush(int(live_summary.ended_ns))
    orchestrator.write_outputs(int(live_summary.started_ns), int(live_summary.ended_ns), "replay")
    if args.diagnostic_output_only:
        result = {
            "status": "PASSED_DIAGNOSTIC_REPLAY",
            "evidence_label": "BACKFILLED_DIAGNOSTIC_REPLAY",
            "replayed_events": replayed_events,
            "inserted_events": inserted_events,
            "source_experiment_modified": False,
            "production_exchange_orders": 0,
        }
        (replay_root / "replay_validation.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 0
    mismatches: list[dict[str, str]] = []
    csv_artifacts = {
        "strategy_summary": "strategy_case_summary.csv",
        "daily_turnover": "daily_turnover.csv",
        "maker_orders": "orders/simulated_orders.csv",
        "direct_and_maker_fills": "fills/simulated_fills.csv",
        "funding": "funding/funding_summary.csv",
        "fees": "fees/fee_summary.csv",
    }
    for artifact, relative in csv_artifacts.items():
        _compare_csv(
            live_root / relative,
            replay_root / relative,
            artifact,
            mismatches,
            candidate_ids,
        )
    _compare_account_state(
        live_root / "strategy_state/final_account_state.json",
        replay_root / "strategy_state/final_account_state.json",
        candidate_ids,
        mismatches,
    )
    for candidate in manifest.experiment_candidate_id.astype(str):
        _compare_bytes(
            live_root / "decisions" / f"{candidate}.jsonl",
            replay_root / "decisions" / f"{candidate}.jsonl",
            "strategy_signals",
            mismatches,
        )
    pd.DataFrame(
        mismatches,
        columns=["artifact", "candidate_id", "field", "detail"],
    ).to_csv(
        live_root / "replay_validation.csv", index=False
    )
    required_artifacts = [*csv_artifacts, "positions_and_account_state", "strategy_signals"]
    artifact_mismatch_counts = {
        artifact: sum(row["artifact"] == artifact for row in mismatches)
        for artifact in required_artifacts
    }
    result = {
        "status": "PASSED" if not mismatches else "BLOCKED",
        "mismatch_count": len(mismatches),
        "replayed_events": replayed_events,
        "required_artifacts": required_artifacts,
        "artifact_mismatch_counts": artifact_mismatch_counts,
        "signals_identical": artifact_mismatch_counts["strategy_signals"] == 0,
        "direct_and_maker_fills_identical": artifact_mismatch_counts["direct_and_maker_fills"] == 0,
        "positions_identical": artifact_mismatch_counts["positions_and_account_state"] == 0,
        "turnover_identical": artifact_mismatch_counts["daily_turnover"] == 0,
        "funding_identical": artifact_mismatch_counts["funding"] == 0,
        "fees_identical": artifact_mismatch_counts["fees"] == 0,
        "pnl_identical": artifact_mismatch_counts["strategy_summary"] == 0,
    }
    (replay_root / "replay_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if not mismatches else 2


if __name__ == "__main__":
    raise SystemExit(main())
