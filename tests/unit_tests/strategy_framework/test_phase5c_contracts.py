from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from strategy_framework.registry import get_entry
from strategy_framework.workbook_dsl import RuleEvaluator, RuleState


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def test_fill_anchors_follow_executed_fills() -> None:
    state = RuleState()
    state.synchronize_fill(position=0.5, fill_price=100.0)
    assert state.first_entry_price == 100.0
    assert state.average_entry_price == 100.0
    assert state.latest_add_fill_price == 100.0
    state.synchronize_fill(position=1.0, fill_price=110.0)
    assert state.first_entry_price == 100.0
    assert state.average_entry_price == 105.0
    assert state.latest_add_fill_price == 110.0
    state.synchronize_fill(position=0.5, fill_price=120.0)
    assert state.average_entry_price == 105.0
    state.synchronize_fill(position=-0.5, fill_price=90.0)
    assert state.first_entry_price == 90.0
    assert state.average_entry_price == 90.0
    state.synchronize_fill(position=0.0, fill_price=95.0)
    assert state.first_entry_price is None
    assert state.average_entry_price is None
    assert state.latest_add_fill_price is None


def test_fill_anchored_arithmetic_and_since_entry_extrema() -> None:
    rule = {
        "schema_version": 2,
        "features": [],
        "actions": [{
            "action": "EXIT_LONG", "fraction": 1.0,
            "condition": {
                "op": "lte", "left": "p5c_close",
                "right": {"op": "sub", "left": {"op": "average_entry_price"},
                          "right": {"op": "mul", "left": "p5c_atr14", "right": 1.0}},
            },
        }],
    }
    evaluator = RuleEvaluator(rule)
    assert evaluator.select_action({"p5c_close": 95.0, "p5c_atr14": 5.0, "p5c_high": 101.0, "p5c_low": 94.0}) is None
    evaluator.synchronize_fill(position=1.0, fill_price=100.0)
    selected = evaluator.select_action({"p5c_close": 95.0, "p5c_atr14": 5.0, "p5c_high": 102.0, "p5c_low": 94.0})
    assert selected is not None and selected.action.value == "EXIT_LONG"
    assert evaluator.state.highest_since_entry == 102.0
    assert evaluator.state.lowest_since_entry == 94.0
    assert evaluator.state.bars_since_entry == 1


def test_phase5c_contracts_and_closure_are_frozen_and_complete() -> None:
    contracts = json.loads((ROOT / "configs/semantic_contracts/workbook_phase5c_contracts.json").read_text(encoding="utf-8"))
    assert contracts["frozen_before_backtest"] is True
    assert len(contracts["contracts"]) == 6
    plan = json.loads((ROOT / "configs/semantic_contracts/workbook_phase5c_strategies.json").read_text(encoding="utf-8"))
    assert len(plan) == 40
    assert all(item["remaining_blockers"] == [] for item in plan.values())
    assert all(item["source_timeframe"] in {"1m", "1d"} for item in plan.values())
    with (AUDIT / "phase5c_semantic_parameter_gap_audit.csv").open(encoding="utf-8-sig", newline="") as stream:
        audit = list(csv.DictReader(stream))
    assert len(audit) == 1029
    assert len({row["source_identity"] for row in audit}) == 1029
    recovered = {row["source_identity"] for row in audit if row["remaining_blockers"] == ""}
    assert recovered == set(plan)


@pytest.mark.parametrize("strategy_id", sorted(json.loads(
    (ROOT / "configs/semantic_contracts/workbook_phase5c_strategies.json").read_text(encoding="utf-8")
)))
def test_every_phase5c_strategy_is_registered(strategy_id: str) -> None:
    entry = get_entry(strategy_id)
    assert entry.name == strategy_id
    assert (ROOT / "strategies" / strategy_id / "config.yaml").is_file()


def test_phase5c_modelled_exposure_never_exceeds_one_x() -> None:
    plan = json.loads((ROOT / "configs/semantic_contracts/workbook_phase5c_strategies.json").read_text(encoding="utf-8"))
    for item in plan.values():
        defaults = item.get("defaulted_parameters", {})
        fraction = float(defaults.get("fraction", 1.0))
        stages = int(defaults.get("layers", defaults.get("stages", 1)))
        assert fraction * stages <= 1.0 + 1e-12
