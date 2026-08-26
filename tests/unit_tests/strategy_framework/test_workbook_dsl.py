from __future__ import annotations

import pytest

from strategy_framework.workbook_dsl import ActionType, RuleEvaluator, validate_rule


def rule(actions):
    return {"schema_version": 2, "features": [], "actions": actions}


def test_exit_precedes_reduce_and_entry_and_position_is_fill_synchronized() -> None:
    evaluator = RuleEvaluator(rule([
        {"action": "ENTER_LONG", "condition": {"op": "true"}},
        {"action": "REDUCE_CURRENT", "condition": {"op": "true"}, "fraction": 0.5},
        {"action": "EXIT_LONG", "condition": {"op": "position_is", "side": "long"}},
    ]))
    assert evaluator.select_action({}).action is ActionType.ENTER_LONG
    evaluator.synchronize_fill(position=1.0, fill_price=100.0)
    assert evaluator.select_action({}).action is ActionType.EXIT_LONG


def test_cross_previous_and_consecutive_use_only_committed_observations() -> None:
    evaluator = RuleEvaluator(rule([{
        "action": "ENTER_LONG",
        "condition": {"op": "and", "args": [
            {"op": "cross_above", "left": "fast", "right": "slow"},
            {"op": "consecutive", "bars": 2, "arg": {"op": "gt", "left": "close", "right": 0}},
        ]},
    }]))
    assert evaluator.select_action({"fast": 1, "slow": 2, "close": 1}) is None
    action = evaluator.select_action({"fast": 3, "slow": 2, "close": 1})
    assert action is not None and action.action is ActionType.ENTER_LONG


def test_invalid_or_executable_python_expression_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        validate_rule(rule([{"action": "ENTER_LONG", "condition": {"op": "eval", "code": "open('x')"}}]))


def test_only_selected_action_commits_state_transition() -> None:
    evaluator = RuleEvaluator(rule([
        {"action": "ENTER_LONG", "condition": {
            "op": "state_transition", "state": "episode", "from": "IDLE", "to": "ENTERED"
        }},
        {"action": "EXIT_ALL", "condition": {
            "op": "state_transition", "state": "episode", "from": "IDLE", "to": "EXITED"
        }},
    ]))
    # FLAT makes EXIT_ALL inapplicable, so its higher priority must not mutate state.
    assert evaluator.select_action({}).action is ActionType.ENTER_LONG
    assert evaluator.state.flags["episode"] == "ENTERED"
