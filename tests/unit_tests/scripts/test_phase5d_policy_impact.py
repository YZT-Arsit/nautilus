from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from scripts.internal import build_phase5d_policy_impact as audit


ROOT = Path(__file__).resolve().parents[3]


def synthetic(policy_ids: list[str], fingerprint: str, irreducible: bool = False) -> dict[str, object]:
    return {"minimum_policies": policy_ids, "semantic_fingerprint": fingerprint, "irreducible": irreducible}


def test_intrusiveness_contract_is_complete_and_deterministic() -> None:
    assert len(audit.POLICIES) == len(audit.POLICY_BY_ID)
    assert {item.intrusiveness for item in audit.POLICIES} == {"LOW", "MEDIUM", "HIGH", "VERY_HIGH"}
    assert audit.max_intrusiveness(["EXISTING_DEFAULT_PROPAGATION"]) == "LOW"
    assert audit.max_intrusiveness(["EXISTING_DEFAULT_PROPAGATION", "MODELLED_BOUNDED_EQUAL_LADDER"]) == "MEDIUM"


def test_minimum_policy_resolution_examples() -> None:
    policy, _ = audit.resolve_blocker("LEVEL_TOLERANCE_UNDEFINED", "价格回踩 ma20 支撑")
    assert policy == "EXISTING_LEVEL_TOLERANCE_PROPAGATION"
    policy, _ = audit.resolve_blocker("DIVERGENCE_DEFINITION_INCOMPLETE", "rsi 底背离")
    assert policy == "STANDARD_REGULAR_DIVERGENCE"
    policy, _ = audit.resolve_blocker("DIVERGENCE_DEFINITION_INCOMPLETE", "指标背离")
    assert policy == "DEFAULT_RSI_DIVERGENCE"


def test_single_and_multi_policy_full_closure() -> None:
    row = synthetic(["EXISTING_DEFAULT_PROPAGATION", "MODELLED_BOUNDED_EQUAL_LADDER"], "a")
    assert not audit.full_closure(row, {"EXISTING_DEFAULT_PROPAGATION"})
    assert audit.full_closure(row, {"EXISTING_DEFAULT_PROPAGATION", "MODELLED_BOUNDED_EQUAL_LADDER"})
    assert not audit.full_closure(synthetic([], "b", irreducible=True), set(audit.POLICY_BY_ID))


def test_semantic_group_deduplication() -> None:
    rows = [synthetic([], "same"), synthetic([], "same"), synthetic([], "different")]
    assert audit.unique_groups(rows) == 2


def test_policy_frontier_scenario_uses_set_union_not_sum() -> None:
    rows = [synthetic(["EXISTING_DEFAULT_PROPAGATION"], "a"),
            synthetic(["EXISTING_DEFAULT_PROPAGATION", "EXISTING_LEVEL_TOLERANCE_PROPAGATION"], "b")]
    one = audit.scenario("one", {"EXISTING_DEFAULT_PROPAGATION"}, rows)
    both = audit.scenario("both", {"EXISTING_DEFAULT_PROPAGATION", "EXISTING_LEVEL_TOLERANCE_PROPAGATION"}, rows)
    assert one["rows_fully_unlocked"] == 1
    assert both["rows_fully_unlocked"] == 2


def test_989_row_reconciliation_and_performance_exclusion() -> None:
    validation = audit.build()
    assert validation["starting_rows"] == validation["audited_rows"] == 989
    assert validation["missing_rows"] == 0
    decision = audit.read_csv(audit.AUDIT / "phase5d_policy_decision_table.csv")
    dependency = audit.read_csv(audit.AUDIT / "phase5d_policy_dependency_audit.csv")
    assert len(dependency) == 989
    assert not (set(decision[0]) & audit.PERFORMANCE_COLUMNS)
    assert validation["performance_metrics_used_for_policy_selection"] is False


def test_no_registry_mutation_from_audit_build() -> None:
    registry = ROOT / "strategy_framework/registry.py"
    before = hashlib.sha256(registry.read_bytes()).hexdigest()
    audit.build()
    after = hashlib.sha256(registry.read_bytes()).hexdigest()
    assert before == after


def test_counterfactual_minimum_policy_rows_are_unique() -> None:
    rows = audit.read_csv(audit.AUDIT / "phase5d_counterfactual_strategy_status.csv")
    assert len(rows) == 989
    assert len({row["source_identity"] for row in rows}) == 989

