#!/usr/bin/env python3
"""Durable active-active qualification -> clean 24h A/B delivery pipeline."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(r"D:\nautilus")
PARENT = REPO / "paper_trading/experiments/paper_20260928_4c9ee2b28d67"
FAILED_6H = REPO / "paper_trading/experiments/paper_endurance_6h_20261006_043302"
AUDIT = REPO / "outputs/baseline_evaluation/paper_market_data_continuity_repair"
DELIVERY = REPO / "outputs/deliverables/direct_maker_24h_clean_ab"
CANDIDATE = "pc_2fe14acb95eac19f88d4"
WARMUP = REPO / "paper_trading/experiments/paper_regression_30m_20261006_035751/workers/BTCUSDT/warmup/BTCUSDT_1m.jsonl"
STATUS = AUDIT / "active_active_delivery_status.json"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {**value, "updated_at": datetime.now(timezone.utc).isoformat(),
             "production_exchange_orders": 0, "p1": "FROZEN_PASSED",
             "demo": "WAITING_FOR_CREDENTIALS", "seven_day": "NOT_STARTED"}
    contents = json.dumps(value, indent=2) + "\n"
    if sys.platform == "win32":
        path.write_text(contents, encoding="utf-8")
    else:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(contents, encoding="utf-8")
        temporary.replace(path)


def write_readme(status: dict) -> None:
    DELIVERY.mkdir(parents=True, exist_ok=True)
    text = f"""# DIRECT vs MAKER clean 24h status

- Strategy: `dynamic_breakout_short`
- Case: `BTCUSDT / 1m / STRICT_REVERSE`
- Production market data: read-only
- Production orders: 0
- Failed evidence: `paper_clean_ab_20261004_101057` (201/1440); `paper_endurance_6h_20261006_043302` (337/360)
- Root causes: upstream proxy tunnel silence; reconnect-driven architecture insufficient
- Repair: two independent active-active proxy collectors, canonical exchange-ID merger, append-only WAL, independent paper consumer
- Current status: `{status.get('status', 'INITIALIZING')}`
- Qualification experiment: `{status.get('qualification_experiment_id', 'NOT_STARTED')}`
- 24h experiment: `{status.get('new_experiment_id', 'NOT_STARTED')}`
- Updated UTC: `{datetime.now(timezone.utc).isoformat()}`
"""
    (DELIVERY / "README_STATUS.md").write_text(text, encoding="utf-8")


def set_status(**values) -> None:
    atomic_json(STATUS, values); write_readme(values)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=REPO, check=True)


def prepare(prefix: str, purpose: str) -> tuple[str, Path]:
    identity = f"{prefix}_{utc_stamp()}"
    path = REPO / "paper_trading/experiments" / identity
    run([sys.executable, str(REPO / "scripts/internal/prepare_focused_paper_experiment.py"),
         "--parent", str(PARENT), "--output", str(path), "--experiment-id", identity,
         "--candidate-id", CANDIDATE, "--purpose", purpose])
    return identity, path


def phase(experiment: Path, duration: int, expected: int, inject: bool = False) -> None:
    cmd = [sys.executable, str(REPO / "scripts/internal/launch_active_active_phase.py"),
           "--repo", str(REPO), "--experiment", str(experiment), "--candidate-id", CANDIDATE,
           "--warmup-source", str(WARMUP), "--duration-seconds", str(duration),
           "--expected-bars", str(expected)]
    if inject:
        cmd += ["--inject-reset-route", "ROUTE_B", "--inject-at-seconds", "900"]
    run(cmd)


def validate(experiment: Path, expected: int) -> dict:
    collector = json.loads((experiment / "active_active/collector_validation.json").read_text())
    consumer = json.loads((experiment / "workers/BTCUSDT/dry_run_validation.json").read_text())
    if collector["status"] != "PASSED" or int(collector["canonical_missing_minutes"]) != 0:
        raise RuntimeError("canonical collector coverage gate failed")
    if consumer["status"] != "PASSED" or int(consumer["observed_bars"]) != expected:
        raise RuntimeError("paper consumer bar gate failed")
    return {"collector": collector, "consumer": consumer}


def finalize_delivery(experiment: Path) -> None:
    package = REPO / "scripts/internal/package_demo_paper_ab_resolution.py"
    staging = DELIVERY.parent / f"direct_maker_24h_clean_ab_staging_{utc_stamp()}"
    run([sys.executable, str(package), "--source-experiment", str(experiment), "--output", str(staging)])
    case = staging / "dynamic_breakout_short/BTCUSDT_1m_STRICT_REVERSE"
    for name in ("01_summary", "02_direct", "03_maker", "04_network_validation", "05_replay_validation"):
        (staging / name).mkdir(exist_ok=True)
    shutil.copy2(case / "comparison/execution_comparison.csv", staging / "01_summary/direct_vs_maker_summary.csv")
    shutil.copy2(case / "comparison/comparison.png", staging / "01_summary/comparison.png")
    shutil.copy2(experiment / "workers/BTCUSDT/data_quality_summary.csv", staging / "01_summary/data_quality_summary.csv")
    shutil.copytree(case / "01_DIRECT", staging / "02_direct", dirs_exist_ok=True)
    shutil.copytree(case / "02_MAKER", staging / "03_maker", dirs_exist_ok=True)
    for name in ("collector_validation.json", "canonical_minute_coverage.csv", "route_health_timeline.csv"):
        shutil.copy2(experiment / "active_active" / name, staging / "04_network_validation" / name)
    for name in ("replay_validation.csv",):
        shutil.copy2(experiment / "workers/BTCUSDT" / name, staging / "05_replay_validation" / name)
    if DELIVERY.exists():
        DELIVERY.rename(DELIVERY.parent / f"direct_maker_24h_clean_ab_status_archive_{utc_stamp()}")
    staging.rename(DELIVERY)


def main() -> int:
    AUDIT.mkdir(parents=True, exist_ok=True)
    FAILED_6H.mkdir(parents=True, exist_ok=True)
    atomic_json(FAILED_6H / "FAILED_6H_ENDURANCE_RECONNECT_STORM.json", {
        "label": "FAILED_6H_ENDURANCE_RECONNECT_STORM", "observed_bars": 337,
        "expected_bars": 360, "missing": 23, "reconnects": 54, "route_switches": 1,
        "failure_mechanism": "RECONNECT_DRIVEN_ARCHITECTURE_INSUFFICIENT",
    })
    try:
        set_status(status="RUNNING_ACTIVE_ACTIVE_FAULT_INJECTION")
        run([sys.executable, str(REPO / "scripts/internal/validate_active_active_faults.py"), "--output", str(AUDIT)])
        run([sys.executable, str(REPO / "scripts/internal/probe_active_active_routes.py"),
             "--output", str(AUDIT / "active_active_route_probe.json"), "--seconds", "30"])
        qualification_id, qualification = prepare("paper_active_active_qualification_30m", "ACTIVE_ACTIVE_30M_QUALIFICATION")
        set_status(status="RUNNING_30M_QUALIFICATION", qualification_experiment_id=qualification_id)
        phase(qualification, 1800, 30, inject=True)
        validate(qualification, 30)
        code_paths = [REPO / "strategy_framework/paper_trading/active_active.py",
                      REPO / "scripts/run_active_active_collector.py",
                      REPO / "scripts/run_canonical_paper_consumer.py"]
        digest = hashlib.sha256(b"".join(path.read_bytes() for path in code_paths)).hexdigest()[:12]
        clean_id, clean = prepare(f"paper_clean_active_active_ab_{digest}", "CLEAN_24H_ACTIVE_ACTIVE_DIRECT_MAKER_AB")
        set_status(status="RUNNING_CLEAN_24H", qualification_status="PASSED",
                   qualification_experiment_id=qualification_id, new_experiment_id=clean_id,
                   new_experiment_path=str(clean))
        phase(clean, 86400, 1440)
        validate(clean, 1440)
        worker = clean / "workers/BTCUSDT"
        run([sys.executable, str(REPO / "scripts/internal/replay_paper_experiment.py"),
             "--repo", str(REPO), "--experiment", str(clean), "--phase-root", str(worker),
             "--candidate-id", CANDIDATE])
        replay = worker / "replay_validation.csv"
        if not replay.exists() or len(replay.read_text(encoding="utf-8-sig").splitlines()) > 1:
            raise RuntimeError("offline replay mismatch gate failed")
        finalize_delivery(clean)
        set_status(status="PASSED", qualification_status="PASSED", new_24h_status="COMPLETED",
                   qualification_experiment_id=qualification_id, new_experiment_id=clean_id,
                   result=str(DELIVERY), replay_mismatches=0)
        return 0
    except Exception as exc:
        set_status(status="BLOCKED", exception_type=type(exc).__name__, exception_message=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
