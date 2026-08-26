from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def test_phase5a_full_population_reconciles_and_new_rules_have_no_blockers() -> None:
    with (AUDIT / "phase5a_strategy_closure.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1112
    assert len({row["source_identity"] for row in rows}) == 1112
    plan = json.loads((ROOT / "configs/semantic_contracts/workbook_phase5a_strategies.json").read_text())
    implemented = [row for row in rows if row["phase5a_status"] == "IMPLEMENTED_STANDALONE"]
    assert {row["source_identity"] for row in implemented} == set(plan)
    assert all(not row["remaining_blocker_set"] for row in implemented)


def test_phase5a_contracts_are_frozen_and_modelled_provenance_is_visible() -> None:
    registry = json.loads((ROOT / "configs/semantic_contracts/workbook_phase5a_modelled.json").read_text(encoding="utf-8"))
    assert registry["frozen_before_backtest"] is True
    assert len(registry["contracts"]) >= 14
    assert all(item["provenance"] == "MODELLED_BASELINE_INTERPRETATION" for item in registry["contracts"])
    assert all(item["lookahead_rule"] for item in registry["contracts"])
