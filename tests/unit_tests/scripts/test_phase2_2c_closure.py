import csv
import json
from collections import defaultdict
from pathlib import Path

from scripts.internal.compile_phase2_2c_strategies import compile_definitions


ROOT = Path("outputs/internal_audit/strategy_workbook")


def rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_phase2_2c_compiler_emits_only_complete_blocker_sets() -> None:
    manifest = rows(ROOT / "strategy_workbook_conversion_manifest.csv")
    blockers = rows(ROOT / "semantic_contracts/semantic_blocker_manifest.csv")
    phase2b = set(json.loads(Path(
        "configs/semantic_contracts/workbook_phase2_2b_strategies.json"
    ).read_text(encoding="utf-8")))
    compiled = compile_definitions(manifest, blockers, phase2b)
    assert len(compiled) == 41
    blocker_sets = defaultdict(set)
    for row in blockers:
        blocker_sets[row["source_identity"]].add(row["normalized_blocker_id"])
    assert all(
        blocker_sets[identity] == set(item["resolved_blockers"])
        for identity, item in compiled.items()
    )


def test_phase2_2c_closure_reconciles_every_remaining_strategy() -> None:
    closure = rows(ROOT / "phase2_2c_strategy_closure.csv")
    assert len(closure) == 1153
    assert sum(bool(row["registry_id"]) for row in closure) == 41
    assert all(not row["remaining_blockers"] for row in closure if row["registry_id"])
    assert all(row["remaining_blockers"] for row in closure if not row["registry_id"])
