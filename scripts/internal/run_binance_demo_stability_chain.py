#!/usr/bin/env python3
"""Durable short -> 24h -> conditional 72h Binance Demo stability chain."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED = ["README.md", "config.json", "events.jsonl", "orders.csv", "fills.csv", "health.csv", "faults.json", "report.md"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def append_event(root: Path, event: str, **fields: Any) -> None:
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"local_time": utc_now(), "event": event, **fields}, separators=(",", ":")) + "\n")


def code_revision(repo: Path) -> str:
    digest = hashlib.sha256()
    for relative in [
        "scripts/internal/run_binance_demo_engineering_smoke.py",
        "scripts/internal/run_binance_demo_stability.py",
        "scripts/internal/run_binance_demo_stability_chain.py",
    ]:
        digest.update((repo / relative).read_bytes())
    try:
        git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    except Exception:
        git = "NO_GIT_REVISION"
    return f"git:{git};stability_sha256:{digest.hexdigest()}"


def build_config(repo: Path, run_id: str, stage: str, duration: int, start: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "stage": stage,
        "environment": "FUTURES_DEMO",
        "http_endpoint": "https://demo-fapi.binance.com",
        "ws_endpoint": "wss://demo-fstream.binance.com",
        "symbol": "BTCUSDT",
        "start_epoch": start,
        "end_epoch": start + duration,
        "duration_seconds": duration,
        "code_revision": code_revision(repo),
        "strategy_execution": "BOUNDED_ENGINEERING_ORDERS_ONLY",
        "production_order_routing": "DISABLED",
        "limits": {
            "max_order_notional_usdt": 100.0,
            "max_abs_position_notional_usdt": 100.0,
            "max_open_orders": 1,
            "max_orders_per_minute": 6,
            "post_only_rest_seconds": 3.0,
            "market_cycle_interval_seconds": 21600.0,
            "post_only_cycle_interval_seconds": 1800.0 if stage != "short" else 120.0,
            "stale_market_seconds": 20.0,
            "reconciliation_interval_seconds": 30.0,
        },
        "pass_criteria": {
            "min_runtime_availability": 0.95 if stage == "short" else 0.99,
            "min_rest_success_rate": 0.99,
            "reconciliation_discrepancies": 0,
            "unexpected_duplicate_fills": 0,
            "final_position": 0,
            "final_open_orders": 0,
            "production_orders": 0,
        },
    }


def init_root(root: Path, repo: Path, short_seconds: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    readme = """# Binance Futures Demo Stability

This package evaluates operational execution stability, not strategy quality or profitability.

- Environment is hard-locked to Binance USD-M Futures Demo.
- Production order routing is never initialized; production orders must remain zero.
- One isolated Demo controller is used; DIRECT/MAKER exchange-native A/B is not run.
- Immutable per-stage evidence is under `runs/`.
- Root files are the current-stage view; completed stage evidence remains preserved under `runs/`.
- Status vocabulary: PASS, FAIL, NOT_TESTED, INCONCLUSIVE.
"""
    (root / "README.md").write_text(readme, encoding="utf-8")
    if not (root / "events.jsonl").exists():
        (root / "events.jsonl").write_text("", encoding="utf-8")
    for name, columns in {
        "orders.csv": ["local_time", "exchange_time", "source", "action", "client_order_id", "order_id", "side", "order_type", "price", "quantity", "status", "ack_latency_ms", "reason"],
        "fills.csv": ["local_time", "exchange_time", "source", "trade_id", "order_id", "client_order_id", "side", "price", "quantity", "commission", "commission_asset", "duplicate"],
        "health.csv": ["local_time", "runtime_seconds", "rest_healthy", "public_ws_healthy", "user_ws_healthy", "market_age_seconds", "rest_success", "rest_failure", "public_ws_reconnects", "user_ws_reconnects", "reconciliation_discrepancies", "duplicate_fills", "position", "open_orders", "submission_enabled"],
    }.items():
        if not (root / name).exists():
            with (root / name).open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(columns)
    suite = {
        "environment": "FUTURES_DEMO",
        "code_revision": code_revision(repo),
        "short_duration_seconds": short_seconds,
        "stages": {"faults": "NOT_TESTED", "short": "NOT_TESTED", "demo_24h": "NOT_TESTED", "demo_72h": "NOT_TESTED"},
        "production_exchange_orders": 0,
        "isolated_direct_maker_ab": "NOT_READY",
    }
    if not (root / "config.json").exists():
        atomic_json(root / "config.json", suite)
    if not (root / "report.md").exists():
        (root / "report.md").write_text("# Binance Demo Stability Status\n\nStatus: **NOT_TESTED**\n", encoding="utf-8")


def publish_current(root: Path, run_dir: Path) -> None:
    # Per-run evidence remains immutable; root is explicitly the current view.
    for name in ["events.jsonl", "orders.csv", "fills.csv", "health.csv", "report.md"]:
        source = run_dir / name
        if source.exists():
            shutil.copy2(source, root / name)


def write_root_report(root: Path, state: dict[str, Any]) -> None:
    lines = [
        "# Binance Demo Stability Status", "",
        f"Updated: {utc_now()}", "",
        f"- Fault injection: **{state['stages']['faults']}**",
        f"- Short gate: **{state['stages']['short']}**",
        f"- 24-hour run: **{state['stages']['demo_24h']}**",
        f"- 72-hour run: **{state['stages']['demo_72h']}**",
        "- Production orders: **0**", "",
        "The 72-hour stage starts only if the 24-hour stage is PASS.",
        "Exchange-native isolated DIRECT/MAKER A/B remains NOT_READY because a second independent Demo account is unavailable.",
    ]
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    atomic_json(root / "config.json", state)


def stage_run(repo: Path, root: Path, state: dict[str, Any], stage: str, duration: int) -> bool:
    existing = state.get("runs", {}).get(stage)
    if existing:
        run_id = existing["run_id"]
        run_dir = Path(existing["path"])
        config_path = run_dir / "config.json"
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            if prior.get("status") in {"PASS", "FAIL", "INCONCLUSIVE"}:
                state["runs"][stage]["status"] = prior["status"]
                state["stages"][stage] = prior["status"]
                atomic_json(root / "chain_state.json", state)
                write_root_report(root, state)
                return prior["status"] == "PASS"
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"demo_{stage}_{stamp}_{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}"
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        atomic_json(config_path, build_config(repo, run_id, stage, duration, time.time()))
        state.setdefault("runs", {})[stage] = {"run_id": run_id, "path": str(run_dir), "status": "RUNNING"}
        state["stages"][stage] = "RUNNING"
        write_root_report(root, state)
        atomic_json(root / "chain_state.json", state)
        append_event(root, "STAGE_STARTED", stage=stage, run_id=run_id)
    command = [
        sys.executable, str(repo / "scripts/internal/run_binance_demo_stability.py"),
        "--output", str(run_dir), "--config", str(config_path), "--allow-demo-orders",
    ]
    result = subprocess.run(command, cwd=repo, check=False)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {"status": "INCONCLUSIVE"}
    status = summary.get("status", "INCONCLUSIVE")
    state["runs"][stage]["status"] = status
    state["stages"][stage] = status
    atomic_json(root / "chain_state.json", state)
    publish_current(root, run_dir)
    write_root_report(root, state)
    append_event(root, "STAGE_FINISHED", stage=stage, run_id=run_id, status=status, exit_code=result.returncode)
    return status == "PASS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--short-seconds", type=int, default=180)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    root = args.root.resolve()
    init_root(root, repo, args.short_seconds)
    state_path = root / "chain_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    else:
        state = json.loads((root / "config.json").read_text(encoding="utf-8"))
        state["runs"] = {}
    state["code_revision"] = code_revision(repo)
    # Simulated evidence is clearly separated from exchange-observed evidence.
    fault_command = [sys.executable, str(repo / "scripts/internal/run_binance_demo_stability.py"), "--output", str(root), "--faults-only"]
    fault_result = subprocess.run(fault_command, cwd=repo, check=False)
    fault_payload = json.loads((root / "faults.json").read_text(encoding="utf-8"))
    state["stages"]["faults"] = fault_payload.get("status", "INCONCLUSIVE")
    atomic_json(state_path, state)
    if fault_result.returncode != 0 or state["stages"]["faults"] != "PASS":
        write_root_report(root, state)
        return 2
    if state["stages"].get("short") != "PASS":
        if state["stages"].get("short") in {"FAIL", "INCONCLUSIVE"}:
            write_root_report(root, state)
            return 2
        if not stage_run(repo, root, state, "short", args.short_seconds):
            atomic_json(state_path, state)
            return 2
        atomic_json(state_path, state)
    if state["stages"].get("demo_24h") != "PASS":
        if state["stages"].get("demo_24h") in {"FAIL", "INCONCLUSIVE"}:
            write_root_report(root, state)
            return 2
        if not stage_run(repo, root, state, "demo_24h", 24 * 60 * 60):
            atomic_json(state_path, state)
            return 2
        atomic_json(state_path, state)
    if state["stages"].get("demo_72h") != "PASS":
        if state["stages"].get("demo_72h") in {"FAIL", "INCONCLUSIVE"}:
            write_root_report(root, state)
            return 2
        passed = stage_run(repo, root, state, "demo_72h", 72 * 60 * 60)
        atomic_json(state_path, state)
        return 0 if passed else 2
    atomic_json(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
