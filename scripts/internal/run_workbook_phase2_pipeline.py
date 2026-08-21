#!/usr/bin/env python3
"""Server-resilient Phase-2 workbook audit, tests, backtests and reporting."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.internal.audit_strategy_workbook import HEADERS, IMPLEMENTED, write_csv


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def command(stage: str, argv: list[str], status_path: Path) -> None:
    atomic_json(status_path, {"status": "running", "stage": stage, "updated_at_utc": datetime.now(UTC).isoformat()})
    print(f"STAGE {stage}: {' '.join(argv)}", flush=True)
    subprocess.run(argv, cwd=REPOSITORY_ROOT, check=True)


def merge_deliverables(deliverable_root: Path, parts: list[Path]) -> None:
    """Merge independently rendered timeframe groups without moving figures."""
    import pandas as pd

    summaries = [pd.read_csv(part / "canonical_summary.csv") for part in parts]
    directions = [pd.read_csv(part / "direction_validation_summary.csv") for part in parts]
    validations = [
        json.loads((part / "validation_summary.json").read_text(encoding="utf-8"))
        for part in parts
    ]
    summary = pd.concat(summaries, ignore_index=True).sort_values(
        ["strategy", "granularity", "lag", "variant", "premium"]
    )
    direction = pd.concat(directions, ignore_index=True).sort_values(
        ["strategy", "case", "variant"]
    )
    summary.to_csv(deliverable_root / "canonical_summary.csv", index=False)
    direction.to_csv(deliverable_root / "direction_validation_summary.csv", index=False)
    (deliverable_root / "canonical_summary.html").write_text(
        summary.to_html(index=False, escape=False), encoding="utf-8"
    )
    validation = {
        "status": "passed" if all(item["status"] == "passed" for item in validations) else "failed",
        "strategy_count": int(summary["strategy"].nunique()),
        "summary_rows": len(summary),
        "direction_validation_failures": sum(item["direction_validation_failures"] for item in validations),
        "global_break_even_maximum_residual": max(item["global_break_even_maximum_residual"] for item in validations),
        "per_trade_break_even_maximum_residual": max(item["per_trade_break_even_maximum_residual"] for item in validations),
    }
    atomic_json(deliverable_root / "validation_summary.json", validation)
    atomic_json(deliverable_root / "artifact_manifest.json", {
        "strategy_count": validation["strategy_count"], "summary_rows": len(summary),
        "parts": [str(part) for part in parts], "validation": validation,
    })


def update_manifests(audit_root: Path, backtest_root: Path, deliverable_root: Path) -> None:
    path = audit_root / "strategy_workbook_conversion_manifest.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row["final_status"] != "implemented":
            continue
        strategy = row["registry_id"]
        source_timeframe = str(IMPLEMENTED[strategy].get("source_timeframe", "1m"))
        cases = ("1d_lag0", "1d_lag1440") if source_timeframe == "1d" else ("1m_lag0", "1m_lag1")
        complete = all(
            (backtest_root / strategy / case / "timeseries.parquet").is_file()
            and (backtest_root / strategy / case / "summary.json").is_file()
            for case in cases
        )
        row.update(
            registry_status="registered",
            structure_status="passed",
            smoke_status="passed",
            backtest_status="passed" if complete else "failed",
        )
    write_csv(path, HEADERS, rows)
    write_csv(audit_root / "strategy_conversion_manifest.csv", HEADERS, rows)
    write_csv(
        audit_root / "strategy_conversion_review.csv", HEADERS,
        [row for row in rows if row["final_status"] != "implemented"],
    )
    write_csv(
        audit_root / "registered_strategy_manifest.csv", HEADERS,
        [row for row in rows if row["final_status"] == "implemented"],
    )
    shutil.copy2(deliverable_root / "canonical_summary.csv", audit_root / "backtest_summary.csv")
    validation_path = audit_root / "validation_summary.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    result_validation = json.loads((deliverable_root / "validation_summary.json").read_text(encoding="utf-8"))
    validation.update(
        pipeline_status="passed",
        completed_baseline_backtests=sum(row["backtest_status"] == "passed" for row in rows),
        failed_baseline_backtests=sum(row["backtest_status"] == "failed" for row in rows),
        baseline_cases=["1m_lag0m", "1m_lag1m", "1d_lag0m", "1d_lag1440m"],
        directional_variants=["original", "long_only", "short_only", "strict_reverse"],
        result_validation=result_validation,
    )
    atomic_json(validation_path, validation)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=REPOSITORY_ROOT / "时序策略.xlsx")
    parser.add_argument("--market-root", type=Path, default=REPOSITORY_ROOT / "historical_data" / "market_data")
    parser.add_argument("--audit-root", type=Path, default=REPOSITORY_ROOT / "outputs" / "internal_audit" / "strategy_workbook")
    parser.add_argument("--backtest-root", type=Path, default=REPOSITORY_ROOT / "outputs" / "batches" / "workbook_strategies_phase2_1")
    parser.add_argument("--deliverable-root", type=Path, default=REPOSITORY_ROOT / "outputs" / "deliverables" / "workbook_strategies_phase2_1")
    parser.add_argument("--status-path", type=Path, default=REPOSITORY_ROOT / "outputs" / "internal_audit" / "strategy_workbook" / "pipeline_status.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    python = sys.executable
    try:
        command("audit", [python, "scripts/internal/audit_strategy_workbook.py", str(args.workbook), "--output-dir", str(args.audit_root)], args.status_path)
        command("package_check", [python, "scripts/internal/generate_workbook_strategy_packages.py", "--check"], args.status_path)
        command(
            "contract_validation",
            [python, "scripts/internal/validate_workbook_conversion.py", "--workbook", str(args.workbook),
             "--output", str(args.audit_root / "contract_validation.json")],
            args.status_path,
        )
        command(
            "targeted_tests",
            [python, "-m", "pytest", "-q", "--confcutdir=tests/unit_tests",
             "tests/unit_tests/feature_engine/test_phase2_1_features.py",
             "tests/unit_tests/feature_engine/test_multitimeframe.py",
             "tests/unit_tests/strategy_framework/test_conditions.py",
             "tests/unit_tests/strategy_framework/test_modules.py",
             "tests/unit_tests/strategies/test_workbook_parametric.py",
             "tests/unit_tests/scripts/test_strategy_workbook_audit.py"],
            args.status_path,
        )
        intraday = sorted(
            strategy for strategy, definition in IMPLEMENTED.items()
            if definition.get("source_timeframe", "1m") == "1m"
        )
        daily = sorted(
            strategy for strategy, definition in IMPLEMENTED.items()
            if definition.get("source_timeframe", "1m") == "1d"
        )
        backtest = [
            python, "-m", "scripts.internal.run_all_strategy_timeframe_lag",
            "--source-root", "strategies", "--market-root", str(args.market_root),
            "--output-root", str(args.backtest_root), "--start", "2021-07-01", "--end", "2026-06-30",
            "--case", "1m:0", "--case", "1m:1", "--continue-on-error",
        ]
        for strategy in intraday:
            backtest.extend(["--strategy", strategy])
        command("five_year_backtests_intraday", backtest, args.status_path)
        if daily:
            daily_backtest = [
                python, "-m", "scripts.internal.run_all_strategy_timeframe_lag",
                "--source-root", "strategies", "--market-root", str(args.market_root),
                "--output-root", str(args.backtest_root), "--start", "2021-07-01", "--end", "2026-06-30",
                "--case", "1d:0", "--case", "1d:1440", "--continue-on-error",
            ]
            for strategy in daily:
                daily_backtest.extend(["--strategy", strategy])
            command("five_year_backtests_daily", daily_backtest, args.status_path)
        render = [
            python, "-m", "scripts.internal.build_all_strategy_timeframe_lag",
            "--batch-root", str(args.backtest_root), "--output-dir", str(args.deliverable_root / "intraday"),
            "--canonical-layout", "--source-config-root", "strategies", "--symbol", "BTCUSDT",
            "--case", "1m_lag0", "--case", "1m_lag1", "--workers", "4", "--overwrite",
        ]
        for strategy in intraday:
            render.extend(["--strategy", strategy])
        command("reporting_intraday", render, args.status_path)
        if daily:
            daily_render = [
                python, "-m", "scripts.internal.build_all_strategy_timeframe_lag",
                "--batch-root", str(args.backtest_root), "--output-dir", str(args.deliverable_root / "daily"),
                "--canonical-layout", "--source-config-root", "strategies", "--symbol", "BTCUSDT",
                "--case", "1d_lag0", "--case", "1d_lag1440", "--workers", "2", "--overwrite",
            ]
            for strategy in daily:
                daily_render.extend(["--strategy", strategy])
            command("reporting_daily", daily_render, args.status_path)
        merge_deliverables(
            args.deliverable_root,
            [args.deliverable_root / "intraday"]
            + ([args.deliverable_root / "daily"] if daily else []),
        )
        update_manifests(args.audit_root, args.backtest_root, args.deliverable_root)
        archive = shutil.make_archive(
            str(args.deliverable_root), "zip", root_dir=args.deliverable_root,
        )
        atomic_json(args.status_path, {
            "status": "complete", "stage": "complete", "strategy_count": len(IMPLEMENTED),
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "backtest_root": str(args.backtest_root), "deliverable_root": str(args.deliverable_root),
            "deliverable_archive": archive,
        })
        print(f"COMPLETE workbook Phase 2 strategies={len(IMPLEMENTED)}", flush=True)
        return 0
    except Exception as exc:
        atomic_json(args.status_path, {
            "status": "failed", "stage": "failed", "error": repr(exc),
            "failed_at_utc": datetime.now(UTC).isoformat(),
        })
        raise


if __name__ == "__main__":
    raise SystemExit(main())
