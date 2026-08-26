from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path

from strategy_framework.registry import STRATEGY_REGISTRY


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase5e_strategies.json"


def rows(name: str) -> list[dict[str, str]]:
    with (AUDIT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def plan() -> dict[str, dict[str, object]]:
    return json.loads(PLAN.read_text(encoding="utf-8"))


def test_all_989_rows_are_reprocessed_once() -> None:
    closure = rows("phase5e_strategy_closure.csv")
    assert len(closure) == 989
    assert len({r["source_identity"] for r in closure}) == 989


def test_only_phase5d_approved_low_policies_are_active() -> None:
    active = rows("phase5e_active_low_risk_contracts.csv")
    assert len(active) == 6
    assert {r["intrusiveness"] for r in active} == {"LOW"}
    assert {r["status"] for r in active} == {"ACTIVE_PHASE5E"}


def test_medium_and_high_policies_remain_blocked() -> None:
    closure = {r["source_identity"]: r for r in rows("phase5e_strategy_closure.csv")}
    for identity in ("xlsx_s1_0013", "xlsx_s1_0014", "xlsx_s1_0015"):
        assert closure[identity]["phase5e_status"] == "REMAINS_UNRESOLVED"
        assert "DIVERGENCE" in closure[identity]["remaining_blockers"]
    assert closure["xlsx_s1_0028"]["phase5e_status"] == "REMAINS_UNRESOLVED"


def test_source_complete_allowlist_has_zero_unmapped_clauses() -> None:
    compiled = rows("phase5e_compiled_rules.csv")
    assert len(compiled) == len(plan()) == 9
    assert all(int(r["unmapped_material_source_clauses"]) == 0 for r in compiled)


def test_fixpoint_terminates_without_policy_escalation() -> None:
    iterations = rows("phase5e_fixpoint_iterations.csv")
    assert [int(r["newly_closed_identities"]) for r in iterations] == [9, 0]
    summary = json.loads((AUDIT / "phase5e_fixpoint_summary.json").read_text(encoding="utf-8"))
    assert summary["fixpoint_reached"] is True
    assert summary["medium_policies_activated"] == 0
    assert summary["high_policies_activated"] == 0


def test_every_new_package_is_normal_and_registered() -> None:
    for strategy_id in plan():
        package = ROOT / "strategies" / strategy_id
        assert {"__init__.py", "config.py", "strategy.py", "plugin.py", "config.yaml"} <= {
            p.name for p in package.iterdir()
        }
        plugin = importlib.import_module(f"strategies.{strategy_id}.plugin").PLUGIN
        assert STRATEGY_REGISTRY[strategy_id] is plugin
        config = plugin.config_cls()
        assert plugin.build_specs(config)
        plugin.strategy_cls(config)


def test_compiled_timeframe_is_source_valid() -> None:
    p = plan()
    daily = {"xlsx_s1_0477", "xlsx_s2_0040", "xlsx_s2_0166", "xlsx_s2_0477"}
    assert all(p[i]["source_timeframe"] == "1d" for i in daily)
    assert all(p[i]["source_timeframe"] == "1m" for i in set(p) - daily)


def test_feature_gate_does_not_auto_implement_nonunique_candidates() -> None:
    feature_plan = {r["feature_name"]: r for r in rows("phase5e_named_feature_plan.csv")}
    assert feature_plan["DPO"]["formula_unique"] == "false"
    assert feature_plan["SUPERTREND"]["formula_unique"] == "false"
    assert all(r["implementation_required"] == "false" for r in feature_plan.values())


def test_preperformance_equivalence_is_deterministic() -> None:
    execution = rows("phase5e_execution_plan.csv")
    representatives: dict[tuple[str, str], str] = {}
    for row in execution:
        key = (row["rule_hash"], row["source_timeframe"])
        representative = representatives.setdefault(key, row["physical_representative"])
        assert row["physical_representative"] == representative
    assert sum(r["physical_execution"] == "true" for r in execution) == 5

