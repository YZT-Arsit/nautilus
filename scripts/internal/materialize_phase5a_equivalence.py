#!/usr/bin/env python3
"""Materialize Phase 5A logical identities from pre-compute rule-hash groups."""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", type=Path, default=ROOT / "outputs/batches/workbook_strategies_phase5a")
    parser.add_argument("--plan", type=Path, default=ROOT / "configs/semantic_contracts/workbook_phase5a_strategies.json")
    parser.add_argument("--audit-root", type=Path, default=ROOT / "outputs/internal_audit/strategy_workbook")
    parser.add_argument("--output-name", default="phase5a_equivalence_reuse.csv")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for identity, definition in sorted(plan.items()):
        groups[(str(definition["rule_hash"]), str(definition.get("source_timeframe", "1m")))].append(identity)
    rows: list[dict[str, object]] = []
    for (rule_hash, timeframe), identities in sorted(groups.items()):
        representative = identities[0]
        for identity in identities:
            for case in (f"{timeframe}_lag0", f"{timeframe}_lag1"):
                source = args.batch_root / representative / case
                destination = args.batch_root / identity / case
                if not (source / "timeseries.parquet").is_file() or not (source / "summary.json").is_file():
                    raise FileNotFoundError(f"missing representative result: {source}")
                if identity != representative:
                    destination.mkdir(parents=True, exist_ok=True)
                    target = destination / "timeseries.parquet"
                    if target.exists(): target.unlink()
                    try: os.link(source / "timeseries.parquet", target)
                    except OSError: shutil.copy2(source / "timeseries.parquet", target)
                    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
                    for value in summary.values():
                        value["strategy"] = identity; value["host_strategy"] = identity
                        value["semantic_provenance"] = plan[identity]["semantic_provenance"]
                        value["contracts_applied"] = ";".join(plan[identity]["contracts_applied"])
                        value["modelled_interpretations"] = ";".join(plan[identity].get("modelled_interpretations", []))
                        value["physical_result_representative"] = representative
                        value["equivalence_rule_hash"] = rule_hash
                    atomic_json(destination / "summary.json", summary)
                    direction_validation = json.loads((source / "direction_validation.json").read_text(encoding="utf-8"))
                    for item in direction_validation:
                        item["strategy"] = identity
                    atomic_json(destination / "direction_validation.json", direction_validation)
                    with (source / "execution_events.csv").open(encoding="utf-8", newline="") as stream:
                        event_rows = list(csv.DictReader(stream)); event_fields = list(event_rows[0]) if event_rows else []
                    if event_fields:
                        for item in event_rows: item["strategy"] = identity
                        with (destination / "execution_events.csv").open("w", encoding="utf-8", newline="") as stream:
                            writer = csv.DictWriter(stream, fieldnames=event_fields); writer.writeheader(); writer.writerows(event_rows)
                    else:
                        shutil.copy2(source / "execution_events.csv", destination / "execution_events.csv")
                rows.append({
                    "strategy_id": identity, "case": case, "rule_hash": rule_hash,
                    "physical_representative": representative,
                    "physical_execution": identity == representative,
                    "logical_result_path": str(destination.relative_to(ROOT)),
                })
    output = args.audit_root / args.output_name
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    print(json.dumps({"logical_cases": len(rows), "physical_cases": len(groups) * 2,
                      "reused_cases": len(rows) - len(groups) * 2, "semantic_groups": len(groups)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
