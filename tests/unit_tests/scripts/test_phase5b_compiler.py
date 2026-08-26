from __future__ import annotations

import base64
import json
from pathlib import Path

from strategy_framework.workbook_dsl import validate_rule


ROOT = Path(__file__).resolve().parents[3]


def test_phase5b_plan_is_typed_clause_complete_and_collision_free() -> None:
    plan = json.loads(
        (ROOT / "configs/semantic_contracts/workbook_phase5b_strategies.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan
    assert len(plan) == len(set(plan))
    for strategy_id, definition in plan.items():
        assert strategy_id.startswith("xlsx_")
        rule = json.loads(base64.urlsafe_b64decode(definition["params"]["rule_spec_b64"]).decode())
        validate_rule(rule)
        assert rule["source_clause_count"] == 3
        assert rule["features"] and rule["actions"]


def test_phase5b_closure_reconciles_all_1082_inputs() -> None:
    import csv
    with (ROOT / "outputs/internal_audit/strategy_workbook/phase5b_strategy_closure.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1082
    assert len({row["source_identity"] for row in rows}) == 1082
    assert all(row["phase5b_status"] in {"IMPLEMENTED_STANDALONE", "SEMANTICALLY_UNRESOLVED"} for row in rows)


def test_phase5b_packages_registry_and_specs_are_complete() -> None:
    from strategy_framework.registry import get_entry

    plan = json.loads(
        (ROOT / "configs/semantic_contracts/workbook_phase5b_strategies.json").read_text(
            encoding="utf-8"
        )
    )
    required = {"__init__.py", "config.py", "strategy.py", "plugin.py", "config.yaml"}
    for strategy_id in plan:
        package = ROOT / "strategies" / strategy_id
        assert required <= {path.name for path in package.iterdir() if path.is_file()}
        plugin = get_entry(strategy_id)
        config = plugin.config_cls()
        assert plugin.build_specs(config)


def test_phase5b_execution_plan_preserves_source_timeframe() -> None:
    import csv

    with (ROOT / "outputs/internal_audit/strategy_workbook/phase5b_execution_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 53
    assert {row["source_timeframe"] for row in rows} == {"1m", "1d"}
    assert next(row for row in rows if row["strategy_id"] == "xlsx_s2_0688")["source_timeframe"] == "1d"
