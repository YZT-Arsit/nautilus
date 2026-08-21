#!/usr/bin/env python3
"""Run and package the Phase 3A design/validation pipeline without optimization."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
DELIVERABLE = ROOT / "outputs/deliverables/phase3a_parameter_search"
STATUS = AUDIT / "phase3a_pipeline_status.json"
ARTIFACTS = (
    "phase3a_parameter_inventory.csv",
    "phase3a_strategy_timeframe_adaptation.csv",
    "phase3a_parameter_adaptation.csv",
    "phase3a_unsafe_timeframe_conversion.csv",
    "phase3a_defaulted_parameter_priority.csv",
    "phase3a_search_compute_estimate.csv",
    "phase3a_search_execution_plan.csv",
    "parameter_search_manifest.csv",
    "phase3a_walk_forward_protocol.json",
    "phase3a_search_protocol.json",
    "phase3a_validation_summary.json",
    "phase3a_baseline_integrity.json",
)


def atomic_status(value: dict[str, object]) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATUS)


def run(stage: str, command: list[str]) -> None:
    atomic_status(
        {"status": "running", "stage": stage, "updated_at_utc": datetime.now(UTC).isoformat()}
    )
    subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603 - fixed internal argv only


def main() -> int:
    python = sys.executable
    try:
        run("compile", [python, "scripts/internal/compile_phase3a_parameter_search.py"])
        run(
            "taxonomy_tests",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "--confcutdir=tests/unit_tests/strategy_framework",
                "tests/unit_tests/strategy_framework/test_phase3a_parameter_adaptation.py",
            ],
        )
        run(
            "manifest_tests",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "--confcutdir=tests/unit_tests/scripts",
                "tests/unit_tests/scripts/test_phase3a_parameter_compilation.py",
            ],
        )
        DELIVERABLE.mkdir(parents=True, exist_ok=True)
        for name in ARTIFACTS:
            source = AUDIT / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, DELIVERABLE / name)
        archive = shutil.make_archive(str(DELIVERABLE), "zip", root_dir=DELIVERABLE)
        summary = json.loads(
            (AUDIT / "phase3a_validation_summary.json").read_text(encoding="utf-8")
        )
        atomic_status(
            {
                "status": "complete",
                "stage": "complete",
                "finished_at_utc": datetime.now(UTC).isoformat(),
                "summary": summary,
                "deliverable_root": str(DELIVERABLE.resolve()),
                "deliverable_archive": archive,
            }
        )
        return 0
    except Exception as exc:
        atomic_status(
            {
                "status": "failed",
                "stage": "failed",
                "error": repr(exc),
                "failed_at_utc": datetime.now(UTC).isoformat(),
            }
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
