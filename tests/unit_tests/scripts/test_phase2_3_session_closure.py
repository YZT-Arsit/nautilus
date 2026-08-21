from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


AUDIT = Path("outputs/internal_audit/strategy_workbook")


def rows(name: str) -> list[dict[str, str]]:
    with (AUDIT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_all_77_session_rows_have_exactly_one_disposition() -> None:
    closure = rows("phase2_3_session_closure.csv")
    assert len(closure) == 77
    assert len({row["source_identity"] for row in closure}) == 77
    counts = Counter(row["new_status"] for row in closure)
    assert sum(counts.values()) == 77
    assert counts["IMPLEMENTED_SESSION_CONTRACT"] == 13
    for row in closure:
        if row["new_status"].startswith("IMPLEMENTED"):
            assert row["registry_id"]
            assert not row["remaining_blockers"]
        else:
            assert row["remaining_blockers"]


def test_phase2_3_plan_is_registered_without_touching_other_populations() -> None:
    plan = json.loads(Path(
        "configs/semantic_contracts/workbook_phase2_3_strategies.json"
    ).read_text(encoding="utf-8"))
    registered = {row["registry_id"] for row in rows("registered_strategy_manifest.csv")}
    assert len(plan) == 13
    assert set(plan) <= registered
    validation = json.loads((AUDIT / "phase2_3_validation_summary.json").read_text(encoding="utf-8"))
    assert validation["final_executable_standalone"] == 131
    assert validation["final_registered_modules"] == 36
    reconciliation = validation["full_workbook_reconciliation"]
    assert reconciliation["total"] == 1715
    assert reconciliation["unaccounted"] == 0
    assert reconciliation["missing_external"] == 155
    assert reconciliation["remaining_general_ambiguity"] == 1112


def test_traditional_gap_rows_are_not_relabelled_as_utc_boundary_returns() -> None:
    gap_rows = [
        row for row in rows("phase2_3_session_closure.csv")
        if row["new_status"] == "TRADITIONAL_GAP_INCOMPATIBLE"
    ]
    assert len(gap_rows) == 29
    assert all(
        row["remaining_blockers"] == "CLOSED_MARKET_GAP_SEMANTICS_INCOMPATIBLE"
        for row in gap_rows
    )
