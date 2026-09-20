#!/usr/bin/env python3
"""Validate accelerated maker replay against an authoritative reference run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def sorted_csv(root: Path, prefix: str) -> pd.DataFrame:
    paths = sorted(root.glob(f"{prefix}_shard_*.csv"))
    if not paths:
        raise FileNotFoundError(f"no {prefix} CSV under {root}")
    frame = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    keys = [key for key in ("case_key", "variant", "execution_model", "strategy_id") if key in frame]
    return frame.sort_values(keys).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--optimized", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results: dict[str, object] = {}
    for prefix in ("case_mapping", "metrics"):
        left = sorted_csv(args.reference, prefix)
        right = sorted_csv(args.optimized, prefix)
        pd.testing.assert_frame_equal(left, right, check_exact=True, check_dtype=True)
        results[f"{prefix}_rows"] = len(left)

    left_paths = {path.relative_to(args.reference / "paths") for path in (args.reference / "paths").rglob("*.parquet")}
    right_paths = {path.relative_to(args.optimized / "paths") for path in (args.optimized / "paths").rglob("*.parquet")}
    if left_paths != right_paths:
        raise AssertionError(f"path set mismatch: left_only={left_paths-right_paths}, right_only={right_paths-left_paths}")
    for relative in sorted(left_paths):
        left = pd.read_parquet(args.reference / "paths" / relative)
        right = pd.read_parquet(args.optimized / "paths" / relative)
        pd.testing.assert_frame_equal(left, right, check_exact=True, check_dtype=True)
    results.update({"status": "PASSED", "path_files": len(left_paths), "check_exact": True})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
