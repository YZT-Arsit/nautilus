#!/usr/bin/env python3
"""Coordinate one independent paper worker per frozen manifest symbol."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_paper_orchestrator import get_json, recent_warmup  # noqa: E402


def _concat_worker_csv(phase_root: Path, symbols: list[str], name: str) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        path = phase_root / "workers" / symbol / name
        if path.exists():
            try:
                frames.append(pd.read_csv(path))
            except pd.errors.EmptyDataError:
                pass
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    destination = phase_root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False)
    return frame


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--phase", choices=["full_manifest_smoke", "authoritative_24h"], required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    args = parser.parse_args()
    repo, experiment = args.repo.resolve(), args.experiment.resolve()
    manifest = pd.read_csv(experiment / "manifest/paper_candidate_manifest_9symbols.csv")
    symbols = sorted(manifest.symbol.unique())
    phase_root = experiment / ("preflight/full_manifest_smoke" if args.phase == "full_manifest_smoke" else "")
    if args.phase == "authoritative_24h":
        phase_root = experiment
    # Fetch public REST inputs once, serially, before starting any timed worker.
    # This avoids nine workers contending on the read-only tunnel at startup and
    # guarantees an identical metadata/warmup snapshot for each symbol shard.
    cache = phase_root / "preflight_cache"
    (cache / "warmup").mkdir(parents=True, exist_ok=True)
    exchange_info = get_json("/fapi/v1/exchangeInfo")
    (cache / "exchange_info.json").write_text(
        json.dumps(exchange_info, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    now_ms = time.time_ns() // 1_000_000
    for symbol in symbols:
        per_tf = recent_warmup(symbol, now_ms)
        for timeframe in manifest.loc[manifest.symbol.eq(symbol), "timeframe"].unique():
            (cache / "warmup" / f"{symbol}_{timeframe}.jsonl").write_text(
                "".join(json.dumps(asdict(bar), sort_keys=True, separators=(",", ":")) + "\n" for bar in per_tf[timeframe]),
                encoding="utf-8",
            )
    if args.phase == "authoritative_24h":
        freeze_path = experiment / "manifest/paper_experiment_freeze.json"
        freeze = json.loads(freeze_path.read_text())
        if freeze.get("forward_start_timestamp") is not None:
            raise RuntimeError("authoritative forward start already frozen")
        freeze["forward_start_timestamp"] = pd.Timestamp.now(tz="UTC").isoformat()
        freeze["status"] = "RUNNING_24H"
        temporary = freeze_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(freeze, indent=2) + "\n")
        os.replace(temporary, freeze_path)
    logs = phase_root / "worker_logs"
    logs.mkdir(parents=True, exist_ok=True)
    processes = []
    for symbol in symbols:
        log = (logs / f"{symbol}.log").open("w", encoding="utf-8")
        command = [
            sys.executable, str(repo / "scripts/run_paper_orchestrator.py"),
            "--repo", str(repo), "--experiment", str(experiment),
            "--phase", args.phase, "--duration-seconds", str(args.duration_seconds),
            "--symbols", symbol, "--worker-id", symbol,
            "--preflight-cache", str(cache),
        ]
        processes.append((symbol, subprocess.Popen(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT), log))
    results = {}
    try:
        for symbol, process, log in processes:
            code = process.wait()
            log.close()
            results[symbol] = code
    except BaseException:
        for _, process, log in processes:
            if process.poll() is None: process.terminate()
            log.close()
        raise
    validations = []
    for symbol in symbols:
        path = phase_root / "workers" / symbol / "dry_run_validation.json"
        if path.exists(): validations.append(json.loads(path.read_text()))
    aggregate = {
        "status": "PASSED" if len(validations) == len(symbols) and all(v["status"] == "PASSED" for v in validations) else "BLOCKED",
        "phase": args.phase, "worker_exit_codes": results, "symbols": symbols,
        "candidate_count": int(sum(v["candidate_count"] for v in validations)),
        "production_exchange_orders": int(sum(v["production_exchange_orders"] for v in validations)),
        "quote_events": int(sum(v["summary"].get("quote_events", 0) for v in validations)),
        "trade_events": int(sum(v["summary"].get("trade_events", 0) for v in validations)),
        "first_tick_fills": int(sum(v["summary"].get("first_tick_fills", 0) for v in validations)),
        "maker_orders": int(sum(v["summary"].get("maker_orders", 0) for v in validations)),
        "maker_fills": int(sum(v["summary"].get("maker_fills", 0) for v in validations)),
        "maker_full_fill_orders": int(sum(v["summary"].get("maker_full_fill_orders", 0) for v in validations)),
        "maker_partial_fill_orders": int(sum(v["summary"].get("maker_partial_fill_orders", 0) for v in validations)),
        "maker_zero_fill_orders": int(sum(v["summary"].get("maker_zero_fill_orders", 0) for v in validations)),
        "maker_requested_quantity": float(sum(v["summary"].get("maker_requested_quantity", 0.0) for v in validations)),
        "maker_filled_quantity": float(sum(v["summary"].get("maker_filled_quantity", 0.0) for v in validations)),
        "worker_errors": int(sum(len(v["summary"].get("worker_errors", [])) for v in validations)),
        "reconnects": int(sum(sum(v["summary"].get("reconnects", {}).values()) for v in validations)),
    }
    cases = _concat_worker_csv(phase_root, symbols, "strategy_case_summary.csv")
    daily = _concat_worker_csv(phase_root, symbols, "daily_turnover.csv")
    quality = _concat_worker_csv(phase_root, symbols, "data_quality_summary.csv")
    orders = _concat_worker_csv(phase_root, symbols, "orders/simulated_orders.csv")
    fills = _concat_worker_csv(phase_root, symbols, "fills/simulated_fills.csv")
    _concat_worker_csv(phase_root, symbols, "funding/funding_summary.csv")
    _concat_worker_csv(phase_root, symbols, "fees/fee_summary.csv")
    aggregate["strategy_case_rows"] = int(len(cases))
    aggregate["daily_turnover_rows"] = int(len(daily))
    aggregate["simulated_order_rows"] = int(len(orders))
    aggregate["simulated_fill_rows"] = int(len(fills))
    aggregate["data_quality_rows"] = int(len(quality))
    aggregate["maker_quantity_fill_ratio"] = (
        aggregate["maker_filled_quantity"] / aggregate["maker_requested_quantity"]
        if aggregate["maker_requested_quantity"] else None
    )
    complete_daily = daily.loc[daily.complete_utc_day.astype(bool)] if "complete_utc_day" in daily else pd.DataFrame()
    aggregate["avg_daily_turnover_pct"] = (
        float(complete_daily.daily_turnover_pct.mean()) if len(complete_daily) else None
    )
    pd.DataFrame([aggregate]).to_csv(phase_root / "experiment_summary.csv", index=False)
    pd.DataFrame([{key: value for key, value in aggregate.items() if not isinstance(value, (dict, list))}]).to_csv(
        phase_root / "execution_summary.csv", index=False
    )
    (phase_root / "stage_validation.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    (phase_root / "dry_run_validation.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    print(json.dumps(aggregate, indent=2))
    return 0 if aggregate["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
