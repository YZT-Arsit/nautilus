#!/usr/bin/env python3
"""Wait for the server Wave 3 run and package it after successful validation."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROGRESS = ROOT / "outputs/parameter_search/phase3b_wave3/progress.json"


def main() -> int:
    while True:
        if PROGRESS.is_file():
            value = json.loads(PROGRESS.read_text(encoding="utf-8"))
            status = value.get("status")
            if status == "COMPLETE":
                return subprocess.call(  # noqa: S603 - fixed local interpreter and script
                    [sys.executable, str(ROOT / "scripts/internal/package_phase3b_wave3.py")]
                )
            if status in {"FAILED", "VALIDATION_FAILED"}:
                return 2
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
