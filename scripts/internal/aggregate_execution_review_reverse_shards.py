#!/usr/bin/env python3
"""Aggregate reverse-validation shards before optional historical maker sensitivity."""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "outputs/baseline_evaluation/execution_method_and_reverse_review/reverse_validation"


def main() -> None:
    paths = sorted((WORK / "shards").glob("*/reverse_case_comparison.csv"))
    if len(paths) != 9:
        raise ValueError(f"reverse shards incomplete: {len(paths)}/9")
    frame = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    frame.to_csv(WORK / "reverse_case_comparison.csv", index=False)
    candidates = frame[["strategy_id", "semantic_group_id", "symbol", "timeframe"]].drop_duplicates()
    candidates.to_csv(WORK / "reverse_candidate_manifest.csv", index=False)
    validated = frame[frame.reverse_validation_positive.astype(str).str.lower().eq("true")]
    validated.to_csv(WORK / "validated_reverse_candidates.csv", index=False)
    print(f"cases={len(frame)} physical_candidates={len(candidates.drop_duplicates(['semantic_group_id','symbol','timeframe']))} validated={len(validated)}")


if __name__ == "__main__":
    main()
