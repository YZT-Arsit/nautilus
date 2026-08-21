#!/usr/bin/env python3
"""Resumable server pipeline for fully closed Phase 2.2C strategies."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(stage: str, command: list[str], status: Path) -> None:
    atomic_json(status, {"status": "running", "stage": stage, "updated_at_utc": datetime.now(UTC).isoformat()})
    print(f"STAGE {stage}: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=ROOT / "时序策略.xlsx")
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--audit-root", type=Path, default=ROOT / "outputs/internal_audit/strategy_workbook")
    parser.add_argument("--backtest-root", type=Path, default=ROOT / "outputs/batches/workbook_strategies_phase2_2c")
    parser.add_argument("--deliverable-root", type=Path, default=ROOT / "outputs/deliverables/workbook_strategies_phase2_2c")
    parser.add_argument("--status", type=Path, default=ROOT / "outputs/internal_audit/strategy_workbook/phase2_2c_pipeline_status.json")
    args = parser.parse_args()
    python = sys.executable
    plan_path = ROOT / "configs/semantic_contracts/workbook_phase2_2c_strategies.json"
    strategies = sorted(json.loads(plan_path.read_text(encoding="utf-8")))
    try:
        run("compile", [python, "scripts/internal/compile_phase2_2c_strategies.py"], args.status)
        run("package_check", [python, "scripts/internal/generate_workbook_strategy_packages.py", "--check"], args.status)
        run("audit", [python, "scripts/internal/audit_strategy_workbook.py", str(args.workbook),
                      "--output-dir", str(args.audit_root)], args.status)
        run("closure_audit", [python, "scripts/internal/finalize_phase2_2c_audit.py",
                              "--audit-root", str(args.audit_root)], args.status)
        tests = [
            ("contract_tests", "tests/unit_tests/strategy_framework", "tests/unit_tests/strategy_framework/test_semantic_contracts.py", "tests/unit_tests/strategy_framework/test_modules.py"),
            ("multitimeframe_tests", "tests/unit_tests/feature_engine", "tests/unit_tests/feature_engine/test_multitimeframe.py"),
            ("strategy_package_tests", "tests/unit_tests/strategies", "tests/unit_tests/strategies/test_workbook_parametric.py"),
            ("closure_tests", "tests/unit_tests/scripts", "tests/unit_tests/scripts/test_phase2_2c_closure.py"),
            ("execution_tests", "tests/unit_tests/scripts", "tests/unit_tests/scripts/test_timeframe_lag_execution.py"),
        ]
        for stage, confcut, *paths in tests:
            run(stage, [python, "-m", "pytest", "-q", f"--confcutdir={confcut}", *paths], args.status)
        backtest = [
            python, "-m", "scripts.internal.run_all_strategy_timeframe_lag",
            "--source-root", "strategies", "--market-root", str(args.market_root),
            "--output-root", str(args.backtest_root), "--start", "2021-07-01", "--end", "2026-06-30",
            "--case", "1m:0", "--case", "1m:1", "--continue-on-error",
        ]
        for strategy in strategies:
            backtest.extend(["--strategy", strategy])
        run("five_year_backtests", backtest, args.status)
        render = [
            python, "-m", "scripts.internal.build_all_strategy_timeframe_lag",
            "--batch-root", str(args.backtest_root), "--output-dir", str(args.deliverable_root),
            "--canonical-layout", "--source-config-root", "strategies", "--symbol", "BTCUSDT",
            "--case", "1m_lag0", "--case", "1m_lag1", "--workers", "4", "--overwrite",
        ]
        for strategy in strategies:
            render.extend(["--strategy", strategy])
        run("reporting", render, args.status)
        run("final_audit", [python, "scripts/internal/finalize_phase2_2c_audit.py",
                            "--audit-root", str(args.audit_root),
                            "--backtest-root", str(args.backtest_root),
                            "--deliverable-root", str(args.deliverable_root)], args.status)
        archive = shutil.make_archive(str(args.deliverable_root), "zip", root_dir=args.deliverable_root)
        atomic_json(args.status, {
            "status": "complete", "stage": "complete", "strategy_count": len(strategies),
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "backtest_root": str(args.backtest_root), "deliverable_root": str(args.deliverable_root),
            "deliverable_archive": archive,
        })
        return 0
    except Exception as exc:
        atomic_json(args.status, {"status": "failed", "stage": "failed", "error": repr(exc),
                                  "failed_at_utc": datetime.now(UTC).isoformat()})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
