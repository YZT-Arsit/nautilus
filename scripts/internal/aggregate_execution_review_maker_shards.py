#!/usr/bin/env python3
"""Aggregate completed per-symbol maker-comparison shards without recomputation."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--comparison-root", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = (args.work or repo / WORK).resolve()
    root = args.comparison_root.resolve() if args.comparison_root else work / "maker_comparison"
    shards = sorted((root / "shards").glob("*/run_summary.json"))
    if len(shards) != 9:
        raise ValueError(f"maker shards incomplete: {len(shards)}/9")
    summaries = [json.loads(path.read_text()) for path in shards]
    if any(item["status"] != "PASSED" for item in summaries):
        raise ValueError("one or more maker shards did not pass")
    for name in ("execution_metrics", "maker_orders", "maker_fills"):
        paths = sorted((root / "shards").glob(f"*/{name}.csv"))
        pd.concat([pd.read_csv(path) for path in paths], ignore_index=True).to_csv(root / f"{name}.csv", index=False)
    for shard in (root / "shards").iterdir():
        if shard.is_dir() and (shard / "paths").exists():
            shutil.copytree(shard / "paths", root / "paths", dirs_exist_ok=True)
        for reference in shard.glob("minute_reference_*.parquet"):
            shutil.copy2(reference, root / reference.name)
    summary = {
        "status":"PASSED", "symbols":len(shards),
        "physical_selected_cases":sum(int(item["physical_selected_cases"]) for item in summaries),
        "execution_metric_rows":sum(int(item["execution_metric_rows"]) for item in summaries),
        "maker_policy":"GTC_UNTIL_SIGNAL_INVALID", "maker_model":"L1_BBO_MAKER",
        "post_only":True, "queue_position":False, "taker_fallback":False,
    }
    (root / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
