from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.internal import compile_phase5f_strategies as compiler
from strategy_framework.modules import GridPyramidState, PyramidDirection
from strategy_framework.workbook_dsl import RuleEvaluator, validate_condition


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def _divergence_rule(direction: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "features": [],
        "actions": [{
            "action": "ENTER_LONG" if direction == "bullish" else "ENTER_SHORT",
            "condition": {
                "op": "regular_divergence", "price": "low" if direction == "bullish" else "high",
                "indicator": "indicator", "direction": direction, "event_id": direction,
                "side_bars": 2, "lookback": 60,
            },
        }],
    }


def test_phase5f_scope_is_exactly_two_medium_contracts() -> None:
    payload = json.loads((AUDIT / "phase5f_contract_freeze.json").read_text(encoding="utf-8"))
    assert [item["contract_id"] for item in payload["active_contracts"]] == list(compiler.ACTIVE_CONTRACTS)
    assert payload["medium_policy_count"] == 2
    assert payload["high_policy_count"] == payload["very_high_policy_count"] == 0
    assert payload["performance_inspected_for_contract_selection"] is False


def test_bounded_equal_default_and_fill_synchronization() -> None:
    state = GridPyramidState()
    assert state.initial_target(1) == pytest.approx(.25)
    assert state.add_target(price=101, atr=1, direction=PyramidDirection.FAVORABLE) is None
    state.synchronize_fill(position=.25, fill_price=100)
    assert state.add_target(price=101, atr=1, direction=PyramidDirection.FAVORABLE) == pytest.approx(.5)
    assert state.add_target(price=102, atr=1, direction=PyramidDirection.FAVORABLE) is None
    state.synchronize_fill(position=.5, fill_price=101)
    assert state.add_target(price=102, atr=1, direction=PyramidDirection.FAVORABLE) == pytest.approx(.75)
    state.synchronize_fill(position=.75, fill_price=102)
    assert state.add_target(price=103, atr=1, direction=PyramidDirection.FAVORABLE) == pytest.approx(1.0)
    state.synchronize_fill(position=1.0, fill_price=103)
    assert state.add_target(price=104, atr=1, direction=PyramidDirection.FAVORABLE) is None


def test_source_layer_count_and_fraction_overrides() -> None:
    state = GridPyramidState(layers=3, layer_fraction=1 / 3)
    assert state.initial_target(-1) == pytest.approx(-1 / 3)
    explicit = GridPyramidState(layers=3, layer_fractions=(.2, .3, .5))
    assert explicit.initial_target(1) == pytest.approx(.2)
    explicit.synchronize_fill(position=.2, fill_price=100)
    assert explicit.add_target(price=101, atr=1, direction=PyramidDirection.FAVORABLE) == pytest.approx(.5)


def test_ladder_exit_resets_episode_and_short_is_symmetric() -> None:
    state = GridPyramidState()
    assert state.initial_target(-1) == pytest.approx(-.25)
    state.synchronize_fill(position=-.25, fill_price=100)
    assert state.add_target(price=99, atr=1, direction=PyramidDirection.FAVORABLE) == pytest.approx(-.5)
    state.synchronize_fill(position=0, fill_price=99)
    assert state.grid_layer_index == 0 and state.episode_side == 0
    assert state.initial_target(1) == pytest.approx(.25)


@pytest.mark.parametrize("text,expected", [
    ("继续加仓", "GENERIC_ADD_ONLY"),
    ("马丁格尔 1-2-4-8 翻倍加仓", "MARTINGALE"),
    ("四层金字塔加仓", "EXPLICIT_PYRAMID"),
    ("等距网格逐档进场", "EXPLICIT_GRID_OR_LADDER"),
])
def test_ladder_applicability_is_narrow(text: str, expected: str) -> None:
    assert compiler.ladder_classification(text) == expected


def test_bullish_divergence_is_visible_only_after_second_right_bar() -> None:
    evaluator = RuleEvaluator(_divergence_rule("bullish"))
    prices = [5, 4, 3, 4, 5, 4, 3, 2, 3, 4]
    indicators = [20, 15, 10, 15, 20, 24, 22, 20, 25, 30]
    selected = []
    for price, indicator in zip(prices, indicators):
        selected.append(evaluator.select_action({"low": price, "indicator": indicator}))
    assert all(item is None for item in selected[:-1])
    assert selected[-1] is not None


def test_bearish_regular_divergence_same_timestamp_pairing() -> None:
    evaluator = RuleEvaluator(_divergence_rule("bearish"))
    prices = [1, 2, 3, 2, 1, 2, 3, 4, 3, 2]
    indicators = [10, 15, 30, 20, 10, 12, 15, 20, 15, 10]
    selected = [evaluator.select_action({"high": p, "indicator": i}) for p, i in zip(prices, indicators)]
    assert selected[-1] is not None


def test_non_divergence_and_older_than_60_bars_do_not_trigger() -> None:
    evaluator = RuleEvaluator(_divergence_rule("bullish"))
    first_prices = [5, 4, 3, 4, 5]
    first_indicators = [20, 15, 10, 15, 20]
    for p, i in zip(first_prices, first_indicators): evaluator.select_action({"low": p, "indicator": i})
    for index in range(61): evaluator.select_action({"low": 10 + index % 2, "indicator": 30})
    selected = None
    for p, i in zip([5, 4, 2, 4, 5], [30, 25, 20, 25, 30]):
        selected = evaluator.select_action({"low": p, "indicator": i})
    assert selected is None


def test_divergence_schema_rejects_nonfrozen_or_missing_contract_parts() -> None:
    with pytest.raises(ValueError):
        validate_condition({"op": "regular_divergence", "price": "low", "indicator": "rsi",
                            "direction": "bullish", "event_id": "x", "side_bars": 1, "lookback": 60})
    with pytest.raises(ValueError):
        validate_condition({"op": "regular_divergence", "price": "low", "direction": "bullish",
                            "event_id": "x", "side_bars": 2, "lookback": 60})


def test_full_closure_and_negative_policy_boundaries() -> None:
    with (AUDIT / "phase5f_strategy_closure.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 980
    implemented = [row for row in rows if row["phase5f_status"] == "IMPLEMENTED_STANDALONE"]
    assert {row["source_identity"] for row in implemented} == compiler.COMPILED_IDS
    assert all(row["remaining_blockers"] == "" and row["unmapped_material_source_clauses"] == "0" for row in implemented)
    unresolved = {row["source_identity"]: row for row in rows if row["phase5f_status"] == "REMAINS_UNRESOLVED"}
    assert unresolved
    # These rejected populations retain explicit blockers instead of receiving
    # an unauthorized timeframe, exit, accounting, feature, or sizing default.
    assert any("TIMEFRAME" in row["remaining_blockers"] for row in unresolved.values())
    assert any("EXIT" in row["remaining_blockers"] for row in unresolved.values())
    assert any("ACCOUNTING" in row["remaining_blockers"] for row in unresolved.values())
    assert any("FEATURE" in row["remaining_blockers"] or "DATA" in row["remaining_blockers"] for row in unresolved.values())
