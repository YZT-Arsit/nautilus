#!/usr/bin/env python3
"""Validate canonical per-episode break-even artifacts by streaming CSV rows."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--update-manifest", action="store_true")
    args = parser.parse_args()
    root = args.root
    case_dirs = sorted(root.glob("*/BTCUSDT/*/lag*m/*"))
    problems: list[str] = []
    maximum_residual = 0.0
    episode_rows = 0
    for case in case_dirs:
        required = [
            case / "metrics.json",
            case / "per_trade_break_even.csv",
            case / "per_trade_break_even_summary.json",
            *case.glob("*_performance.png"),
            *case.glob("*_per_trade_be.png"),
        ]
        if len(required) != 5 or not all(path.is_file() and path.stat().st_size for path in required):
            problems.append(str(case))
            continue
        summary = json.loads((case / "per_trade_break_even_summary.json").read_text())
        maximum_residual = max(
            maximum_residual,
            *(abs(float(summary[mode]["maximum_break_even_residual"])) for mode in ("included", "excluded")),
        )
        with (case / "per_trade_break_even.csv").open(newline="", encoding="utf-8") as stream:
            seen: set[tuple[str, str]] = set()
            for row in csv.DictReader(stream):
                episode_rows += 1
                key = (row["premium_mode"], row["episode_id"])
                if key in seen:
                    problems.append(f"duplicate episode: {case} {key}")
                seen.add(key)
                if row["variant"] != case.name:
                    problems.append(
                        f"variant mismatch: {case} row={row['variant']}"
                    )
                turnover = float(row["delta_turnover"])
                gross_return = float(row["delta_gross_return"])
                bps = float(row["break_even_bps"])
                if turnover <= 0:
                    problems.append(f"non-positive turnover: {case} episode={row['episode_id']}")
                maximum_residual = max(maximum_residual, abs(gross_return - turnover * bps / 10_000.0))
    png = list(root.rglob("*.png"))
    summary_rows = max(sum(1 for _ in (root / "canonical_summary.csv").open(encoding="utf-8")) - 1, 0)
    result = {
        "strategy_count": len([path for path in root.iterdir() if path.is_dir()]),
        "case_count": len(case_dirs),
        "summary_rows": summary_rows,
        "performance_figure_count": len(list(root.rglob("*_performance.png"))),
        "per_trade_figure_count": len(list(root.rglob("*_per_trade_be.png"))),
        "figure_count": len(png),
        "per_trade_row_count": episode_rows,
        "maximum_break_even_residual": maximum_residual,
        "validation_problems": problems,
    }
    if args.update_manifest:
        path = root / "artifact_manifest.json"
        manifest = json.loads(path.read_text())
        manifest.update(result)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.replace(path)
    print(json.dumps(result, indent=2))
    return int(bool(problems) or maximum_residual > 1e-9)


if __name__ == "__main__":
    raise SystemExit(main())
