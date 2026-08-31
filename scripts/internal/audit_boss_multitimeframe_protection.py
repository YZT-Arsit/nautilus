#!/usr/bin/env python3
"""Capture/verify immutable configs and prior canonical boss deliverables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def protected() -> list[Path]:
    values = sorted((ROOT / "strategies").glob("*/config.yaml"))
    values += [
        ROOT / "outputs/deliverables/all_converted_workbook_strategies/all_converted_workbook_strategies.csv",
        ROOT / "outputs/deliverables/phase7a_final_research_review/phase7a_final_summary.json",
    ]
    return [path for path in values if path.is_file()]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "sha256"])
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["before", "after"])
    parser.add_argument(
        "--root", type=Path,
        default=ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen",
    )
    args = parser.parse_args()
    rows = [{"path": str(path.relative_to(ROOT)), "sha256": digest(path)} for path in protected()]
    output = args.root / f"protected_artifacts_{args.mode}.csv"
    write_csv(output, rows)
    changes = []
    if args.mode == "after":
        before_path = args.root / "protected_artifacts_before.csv"
        with before_path.open(encoding="utf-8-sig") as handle:
            before = {row["path"]: row["sha256"] for row in csv.DictReader(handle)}
        now = {row["path"]: row["sha256"] for row in rows}
        changes = sorted(path for path in set(before) | set(now) if before.get(path) != now.get(path))
        (args.root / "protected_hash_validation.json").write_text(
            json.dumps({"status": "PASSED" if not changes else "FAILED", "changes": changes}, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"mode": args.mode, "files": len(rows), "changes": changes}))
    return 0 if not changes else 2


if __name__ == "__main__":
    raise SystemExit(main())
