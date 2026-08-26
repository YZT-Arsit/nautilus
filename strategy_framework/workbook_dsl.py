"""Typed workbook strategy rule evaluator.

The workbook is a source specification only.  Runtime inputs are validated
JSON-compatible AST nodes; source prose is never evaluated as Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ActionType(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    EXIT_ALL = "EXIT_ALL"
    REDUCE_LONG = "REDUCE_LONG"
    REDUCE_SHORT = "REDUCE_SHORT"
    REDUCE_CURRENT = "REDUCE_CURRENT"
    ADD_LONG = "ADD_LONG"
    ADD_SHORT = "ADD_SHORT"
    FLATTEN = "FLATTEN"


ACTION_PRIORITY = {
    ActionType.FLATTEN: 30, ActionType.EXIT_ALL: 30,
    ActionType.EXIT_LONG: 30, ActionType.EXIT_SHORT: 30,
    ActionType.REDUCE_LONG: 20, ActionType.REDUCE_SHORT: 20,
    ActionType.REDUCE_CURRENT: 20,
    ActionType.ENTER_LONG: 10, ActionType.ENTER_SHORT: 10,
    ActionType.ADD_LONG: 10, ActionType.ADD_SHORT: 10,
}


@dataclass(frozen=True)
class RuleAction:
    action: ActionType
    condition: Mapping[str, Any]
    fraction: float = 1.0
    reason: str = "workbook_dsl"

    def __post_init__(self) -> None:
        if not 0 < self.fraction <= 1:
            raise ValueError("action fraction must be in (0, 1]")


@dataclass
class RuleState:
    executed_position: float = 0.0
    fill_price: float | None = None
    first_entry_price: float | None = None
    average_entry_price: float | None = None
    latest_add_fill_price: float | None = None
    highest_since_entry: float | None = None
    lowest_since_entry: float | None = None
    bars_since_entry: int = 0
    features: dict[str, list[float]] = field(default_factory=dict)
    predicates: dict[str, list[bool]] = field(default_factory=dict)
    flags: dict[str, str | bool | float] = field(default_factory=dict)
    observation_index: int = 0
    divergence_pivots: dict[str, list[tuple[int, float, float]]] = field(default_factory=dict)

    def synchronize_fill(self, *, position: float, fill_price: float) -> None:
        before = self.executed_position
        after = float(position)
        price = float(fill_price)
        if price <= 0:
            raise ValueError("fill price must be positive")
        reversing = before != 0 and after != 0 and before * after < 0
        increasing = after != 0 and (
            before == 0 or reversing or (before * after > 0 and abs(after) > abs(before))
        )
        if after == 0:
            self.first_entry_price = None
            self.average_entry_price = None
            self.latest_add_fill_price = None
            self.highest_since_entry = None
            self.lowest_since_entry = None
            self.bars_since_entry = 0
        elif before == 0 or reversing:
            self.first_entry_price = price
            self.average_entry_price = price
            self.latest_add_fill_price = price
            self.highest_since_entry = price
            self.lowest_since_entry = price
            self.bars_since_entry = 0
        elif increasing:
            added = abs(after) - abs(before)
            if self.average_entry_price is None:
                self.average_entry_price = price
            elif added > 0:
                self.average_entry_price = (
                    self.average_entry_price * abs(before) + price * added
                ) / abs(after)
            self.latest_add_fill_price = price
        self.executed_position = after
        self.fill_price = price

    def commit(self, values: Mapping[str, float], predicate_values: Mapping[str, bool]) -> None:
        for name, value in values.items():
            history = self.features.setdefault(name, [])
            history.append(float(value))
            if len(history) > 512:
                del history[:-512]
        for name, value in predicate_values.items():
            history = self.predicates.setdefault(name, [])
            history.append(bool(value))
            if len(history) > 512:
                del history[:-512]
        self.observation_index += 1


def validate_condition(node: Mapping[str, Any]) -> None:
    op = str(node.get("op", ""))
    if op in {"and", "or", "k_of_m"}:
        args = node.get("args")
        if not isinstance(args, list) or not args:
            raise ValueError(f"{op} requires non-empty args")
        for child in args:
            validate_condition(child)
        if op == "k_of_m" and not 1 <= int(node.get("k", 0)) <= len(args):
            raise ValueError("k_of_m requires 1 <= k <= len(args)")
        return
    if op == "not":
        validate_condition(node["arg"]); return
    if op in {"true", "false", "position_is", "state_is"}:
        return
    if op in {"gt", "gte", "lt", "lte", "eq", "cross_above", "cross_below"}:
        if "left" not in node or "right" not in node:
            raise ValueError(f"{op} requires left and right")
        return
    if op in {"turn_up", "turn_down", "pulse", "rising", "falling"}:
        if "value" not in node:
            raise ValueError(f"{op} requires value")
        return
    if op == "regular_divergence":
        if not all(key in node for key in ("price", "indicator", "direction", "event_id")):
            raise ValueError("regular_divergence requires price/indicator/direction/event_id")
        if str(node["direction"]) not in {"bullish", "bearish"}:
            raise ValueError("regular_divergence direction must be bullish or bearish")
        if int(node.get("side_bars", 2)) != 2 or int(node.get("lookback", 60)) != 60:
            raise ValueError("Phase 5F regular divergence is frozen at 2x2 pivots and 60 bars")
        return
    if op in {"consecutive", "previous_condition"}:
        if op == "consecutive":
            if int(node.get("bars", 0)) <= 0:
                raise ValueError("consecutive bars must be positive")
            validate_condition(node["arg"])
        return
    if op == "state_transition":
        if "state" not in node or "from" not in node or "to" not in node:
            raise ValueError("state_transition requires state/from/to")
        return
    raise ValueError(f"unsupported workbook DSL condition op: {op}")


def validate_rule(rule: Mapping[str, Any]) -> None:
    if int(rule.get("schema_version", 0)) != 2:
        raise ValueError("Phase 5B rule requires schema_version=2")
    actions = rule.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Phase 5B rule requires actions")
    for item in actions:
        RuleAction(ActionType(item["action"]), item["condition"], float(item.get("fraction", 1.0)),
                   str(item.get("reason", "workbook_dsl")))
        validate_condition(item["condition"])


class RuleEvaluator:
    def __init__(self, rule: Mapping[str, Any]) -> None:
        validate_rule(rule)
        self.rule = rule
        self.state = RuleState()
        self._current_predicates: dict[str, bool] = {}
        self._pending_flag_updates: dict[str, str | bool | float] = {}
        self._event_cache: dict[str, bool] = {}

    def _regular_divergence(self, node: Mapping[str, Any], values: Mapping[str, float]) -> bool:
        """Causal regular divergence at a just-confirmed 2-left/2-right price pivot.

        The indicator value is sampled at the exact price-pivot timestamp.  No
        independent indicator-pivot search or nearby-bar optimization occurs.
        """
        event_id = str(node["event_id"])
        if event_id in self._event_cache:
            return self._event_cache[event_id]
        price_name = str(node["price"]); indicator_name = str(node["indicator"])
        price_history = self.state.features.get(price_name, [])
        indicator_history = self.state.features.get(indicator_name, [])
        current_price = values.get(price_name)
        if current_price is None or len(price_history) < 4 or len(indicator_history) < 2:
            self._event_cache[event_id] = False
            return False
        window = price_history[-4:] + [float(current_price)]
        pivot_price = float(window[2])
        pivot_indicator = float(indicator_history[-2])
        direction = str(node["direction"])
        confirmed = (
            pivot_price < min(window[0], window[1], window[3], window[4])
            if direction == "bullish"
            else pivot_price > max(window[0], window[1], window[3], window[4])
        )
        if not confirmed:
            self._event_cache[event_id] = False
            return False
        pivot_index = self.state.observation_index - 2
        pivots = self.state.divergence_pivots.setdefault(event_id, [])
        previous = next(
            (item for item in reversed(pivots) if pivot_index - item[0] <= 60),
            None,
        )
        event = False
        if previous is not None:
            _, previous_price, previous_indicator = previous
            event = (
                pivot_price < previous_price and pivot_indicator > previous_indicator
                if direction == "bullish"
                else pivot_price > previous_price and pivot_indicator < previous_indicator
            )
        if not pivots or pivots[-1][0] != pivot_index:
            pivots.append((pivot_index, pivot_price, pivot_indicator))
            if len(pivots) > 128:
                del pivots[:-128]
        self._event_cache[event_id] = event
        return event

    def synchronize_fill(self, *, position: float, fill_price: float) -> None:
        self.state.synchronize_fill(position=position, fill_price=fill_price)

    def _operand(self, node: Any, values: Mapping[str, float]) -> float | None:
        if isinstance(node, (int, float)):
            return float(node)
        if isinstance(node, str):
            return values.get(node)
        if isinstance(node, Mapping):
            op = str(node.get("op"))
            if op == "previous":
                history = self.state.features.get(str(node["value"]), [])
                lag = int(node.get("lag", 1))
                return history[-lag] if lag > 0 and len(history) >= lag else None
            if op == "position":
                return self.state.executed_position
            if op == "fill_price":
                return self.state.fill_price
            if op == "first_entry_price":
                return self.state.first_entry_price
            if op == "average_entry_price":
                return self.state.average_entry_price
            if op == "latest_add_fill_price":
                return self.state.latest_add_fill_price
            if op == "highest_since_entry":
                return self.state.highest_since_entry
            if op == "lowest_since_entry":
                return self.state.lowest_since_entry
            if op == "bars_since_entry":
                return float(self.state.bars_since_entry)
            if op in {"add", "sub", "mul"}:
                left = self._operand(node["left"], values)
                right = self._operand(node["right"], values)
                if left is None or right is None:
                    return None
                return left + right if op == "add" else left - right if op == "sub" else left * right
            if op == "rolling_mean":
                name = str(node["value"])
                window = int(node["window"])
                if window <= 0:
                    raise ValueError("rolling_mean window must be positive")
                history = list(self.state.features.get(name, []))
                current = values.get(name)
                if bool(node.get("include_current", True)) and current is not None:
                    history.append(float(current))
                if len(history) < window:
                    return None
                return sum(history[-window:]) / window
        raise ValueError(f"invalid workbook DSL operand: {node!r}")

    def evaluate(self, node: Mapping[str, Any], values: Mapping[str, float], key: str = "root") -> bool:
        op = str(node["op"])
        if op in {"and", "or", "k_of_m"}:
            states = [self.evaluate(child, values, f"{key}.{index}") for index, child in enumerate(node["args"])]
            result = all(states) if op == "and" else any(states) if op == "or" else sum(states) >= int(node["k"])
        elif op == "not":
            result = not self.evaluate(node["arg"], values, f"{key}.not")
        elif op in {"true", "false"}:
            result = op == "true"
        elif op in {"gt", "gte", "lt", "lte", "eq"}:
            left, right = self._operand(node["left"], values), self._operand(node["right"], values)
            result = False if left is None or right is None else {
                "gt": left > right, "gte": left >= right, "lt": left < right,
                "lte": left <= right, "eq": left == right,
            }[op]
        elif op in {"cross_above", "cross_below"}:
            current_left, current_right = self._operand(node["left"], values), self._operand(node["right"], values)
            previous_left = self._operand({"op": "previous", "value": node["left"]}, values) if isinstance(node["left"], str) else None
            previous_right = self._operand({"op": "previous", "value": node["right"]}, values) if isinstance(node["right"], str) else current_right
            result = False if None in (current_left, current_right, previous_left, previous_right) else (
                previous_left <= previous_right and current_left > current_right if op == "cross_above"
                else previous_left >= previous_right and current_left < current_right
            )
        elif op in {"turn_up", "turn_down"}:
            history = self.state.features.get(str(node["value"]), [])
            current = values.get(str(node["value"]))
            result = False if current is None or len(history) < 2 else (
                history[-1] <= history[-2] and current > history[-1] if op == "turn_up"
                else history[-1] >= history[-2] and current < history[-1]
            )
        elif op in {"rising", "falling"}:
            history = self.state.features.get(str(node["value"]), [])
            current = values.get(str(node["value"]))
            bars = int(node.get("bars", 1)); sequence = history[-bars:] + ([] if current is None else [current])
            result = len(sequence) == bars + 1 and all(
                (a < b if op == "rising" else a > b) for a, b in zip(sequence, sequence[1:])
            )
        elif op == "pulse":
            value = values.get(str(node["value"])); result = value is not None and value > 0
        elif op == "regular_divergence":
            result = self._regular_divergence(node, values)
        elif op == "position_is":
            position = self.state.executed_position; side = str(node.get("side", "flat"))
            result = position > 0 if side == "long" else position < 0 if side == "short" else abs(position) <= 1e-12
        elif op == "state_is":
            result = self.state.flags.get(str(node["state"])) == node.get("value")
        elif op == "state_transition":
            result = self.state.flags.get(str(node["state"]), node.get("from")) == node.get("from")
            if result:
                self._pending_flag_updates[str(node["state"])] = node.get("to")
        elif op == "previous_condition":
            history = self.state.predicates.get(str(node["key"]), []); lag = int(node.get("lag", 1))
            result = len(history) >= lag and history[-lag]
        elif op == "consecutive":
            inner = self.evaluate(node["arg"], values, f"{key}.inner")
            history_key = f"{key}.consecutive_input"
            prior = self.state.predicates.get(history_key, [])
            result = inner and len(prior) >= int(node["bars"]) - 1 and all(prior[-(int(node["bars"]) - 1):])
            self._current_predicates[history_key] = inner
        else:
            raise ValueError(f"unsupported workbook DSL condition op: {op}")
        self._current_predicates[key] = bool(result)
        return bool(result)

    def select_action(self, values: Mapping[str, float]) -> RuleAction | None:
        if abs(self.state.executed_position) > 1e-12:
            high = values.get("p5c_high")
            low = values.get("p5c_low")
            if high is not None:
                self.state.highest_since_entry = (
                    float(high) if self.state.highest_since_entry is None
                    else max(self.state.highest_since_entry, float(high))
                )
            if low is not None:
                self.state.lowest_since_entry = (
                    float(low) if self.state.lowest_since_entry is None
                    else min(self.state.lowest_since_entry, float(low))
                )
            self.state.bars_since_entry += 1
        self._current_predicates = {}
        self._event_cache = {}
        matched: list[tuple[int, int, RuleAction, dict[str, str | bool | float]]] = []
        for index, raw in enumerate(self.rule["actions"]):
            self._pending_flag_updates = {}
            action = RuleAction(ActionType(raw["action"]), raw["condition"],
                                float(raw.get("fraction", 1.0)), str(raw.get("reason", "workbook_dsl")))
            position = self.state.executed_position
            if action.action in {ActionType.EXIT_LONG, ActionType.REDUCE_LONG} and position <= 0:
                continue
            if action.action in {ActionType.EXIT_SHORT, ActionType.REDUCE_SHORT} and position >= 0:
                continue
            if action.action in {ActionType.EXIT_ALL, ActionType.FLATTEN, ActionType.REDUCE_CURRENT} and abs(position) <= 1e-12:
                continue
            if self.evaluate(action.condition, values, f"action.{index}"):
                matched.append((ACTION_PRIORITY[action.action], -index, action, dict(self._pending_flag_updates)))
        self.state.commit(values, self._current_predicates)
        selected = max(matched, default=(0, 0, None, {}))
        self.state.flags.update(selected[3])
        return selected[2]
