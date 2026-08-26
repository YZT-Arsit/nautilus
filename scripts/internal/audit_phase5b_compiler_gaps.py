#!/usr/bin/env python3
"""Classify every Phase 5A unresolved workbook row for Phase 5B.

This is intentionally an audit, not a permissive compiler.  A row is marked
recoverable only when the workbook text supplies a deterministic rule and the
remaining gap is representational.  Qualitative or parameter-free concepts
remain semantic blockers.
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
SOURCE = AUDIT / "phase5a_remaining_strategy_audit.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def norm(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").upper())


SEMANTIC_TERMS: tuple[tuple[str, str], ...] = (
    (r"(?:顶|底|量价|价格与\w*)背离", "DIVERGENCE_DEFINITION_INCOMPLETE"),
    (r"明显|显著|强势|趋势明显|快速", "QUALITATIVE_DEGREE_UNDEFINED"),
    (r"企稳|承压|止跌|滞涨", "STABILIZATION_OR_REJECTION_UNDEFINED"),
    (r"有效突破|假突破|突破确认", "BREAKOUT_CONFIRMATION_UNDEFINED"),
    (r"中低波动|中高波动|高波动|低波动|低位|高位", "VOLATILITY_REGIME_UNDEFINED"),
    (r"附近|接近|重合|共振支撑|共振压力", "LEVEL_TOLERANCE_UNDEFINED"),
    (r"POC|FVG|HV/GV|TRIN|NH/NL|市场宽度", "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE"),
    (r"分批|分层|逐层|逐格|金字塔", "SIZING_OR_LADDER_INCOMPLETE"),
    (r"浮亏\s*[+-]?\d", "FILL_ANCHORED_RISK_STATE_REQUIRED"),
    (r"盈利\s*[+-]?\d", "FILL_ANCHORED_PROFIT_STATE_REQUIRED"),
    (r"持仓超\s*\d", "HOLDING_DURATION_UNIT_UNCLEAR"),
)


def semantic_gaps(text: str) -> list[str]:
    return sorted({code for pattern, code in SEMANTIC_TERMS if re.search(pattern, text)})


def has_explicit_thresholds(text: str) -> bool:
    return bool(re.search(r"(?:>|<|≥|≤|上穿|下穿|突破|跌破|由负转正|由正转负|\d+)", text))


def classify(row: dict[str, str]) -> dict[str, object]:
    reason = row["irreducible_reason"]
    long_text, short_text, exit_text = row["long_entry_text"], row["short_entry_text"], row["exit_text"]
    all_text = norm(";".join((row["indicator_definition"], long_text, short_text, exit_text)))
    gaps = semantic_gaps(all_text)
    ast: set[str] = {"Condition", "Action"}
    state: set[str] = set()
    timeframe: set[str] = set()
    category = ""

    if reason == "ENTRY_SIDE_NOT_FULLY_REPRESENTABLE":
        if re.search(r"无开空|禁止做空|仅做多|仅持有多", all_text):
            category = "ENTRY_SIDE_EXPLICIT_ONE_SIDED"
            ast.add("OneSidedRule")
        elif long_text.strip() and short_text.strip():
            category = "ENTRY_SIDE_EXPLICIT_LONG_SHORT_COLUMNS"
        elif re.search(r"反向|对称|镜像", all_text):
            category = "ENTRY_SIDE_SYMMETRIC_MIRROR"
            ast.add("MirrorFromSource")
        else:
            category = "ENTRY_SIDE_DIRECTIONAL_WORDING"
        if re.search(r"当前|持仓|空仓|无多头|无空头", all_text):
            category = "ENTRY_SIDE_POSITION_DEPENDENT"
            ast.add("PositionPredicate")
            state.add("EXECUTED_POSITION")
        if re.search(r"再次|重新|回踩|上次|第一次", all_text):
            category = "ENTRY_SIDE_REENTRY_CONDITION"
            ast.add("StateTransition")
            state.update(("BREAKOUT_ARMED", "WAITING_FOR_REENTRY"))
        if re.search(r"加仓|减仓|分层|逐层", all_text):
            category = "ENTRY_SIDE_SCALE_IN_SCALE_OUT"
            ast.add("PositionAction")
            state.add("FILL_SYNCHRONIZED_POSITION")
    elif reason == "COMPLETED_MULTI_TIMEFRAME_OR_SESSION_STATE_NOT_FULLY_PARSEABLE":
        if re.search(r"先|随后|再|等待|回踩", all_text):
            category = "MTF_ARM_THEN_TRIGGER"
            state.add("HTF_ARMED")
        elif re.search(r"开盘|收盘|VWAP|前一日|前一交易日|隔夜", all_text):
            category = "SESSION_STATE_REFERENCE"
            timeframe.add("SessionRef")
        elif re.search(r"同步|全部周期|多周期|日线|周线|小时|分钟", all_text):
            category = "MTF_COMPLETED_STATE_OR_TRIGGER"
        else:
            category = "MTF_TIMEFRAME_REFERENCE_INCOMPLETE"
        ast.update(("TimeframeRef", "LatestCompletedState"))
        timeframe.update(("COMPLETED_BAR_ALIGNMENT", "MTF_STATE_TRIGGER_DISTINCTION"))
        if not re.search(r"(?:\d+\s*(?:M|MIN|分钟|H|小时|日|周)|日线|周线|月线|4H|1H)", all_text):
            gaps.append("TIMEFRAME_SET_INCOMPLETE")
    else:
        category = "STATE_MACHINE_COMPLEX"
        ast.add("StateTransition")
        if re.search(r"突破.*回踩|突破后|回踩", all_text):
            state.update(("BREAKOUT_SEEN", "RETEST_SEEN"))
            category = "STATE_MACHINE_BREAKOUT_RETEST"
        if re.search(r"极值.*拐头|超买.*回落|超卖.*反弹", all_text):
            state.add("EXTREME_ARMED")
            category = "STATE_MACHINE_EXTREME_THEN_TURN"
        if re.search(r"加仓|减仓|分层|逐层|逐格", all_text):
            state.add("FILL_SYNCHRONIZED_POSITION")
            ast.add("PositionAction")
            category = "STATE_MACHINE_POSITION_LIFECYCLE"
        if re.search(r"上次|前一|上一", all_text):
            state.add("PREVIOUS_COMMITTED_STATE")
        if re.search(r"持仓|浮盈|浮亏|入场价", all_text):
            state.update(("EXECUTED_POSITION", "FILL_PRICE"))

    # A deterministic rule needs explicit action columns, numerical predicates,
    # and no unresolved qualitative/data semantics.  This is conservative by design.
    structural = bool(long_text.strip() or short_text.strip()) and bool(exit_text.strip())
    numeric = has_explicit_thresholds(all_text)
    semantic_complete = structural and numeric and not gaps
    state_complete = semantic_complete or not state
    compiler_complete = False
    recoverable = semantic_complete and bool(ast or state or timeframe)
    if gaps:
        sem_category = ";".join(sorted(set(gaps)))
        reason_text = "Workbook/approved contracts do not uniquely determine: " + sem_category
    elif recoverable:
        sem_category = "NONE"
        reason_text = "Semantics are explicit; blocked only by typed AST/state/timeframe representation."
    else:
        sem_category = "STRUCTURAL_RULE_INCOMPLETE"
        reason_text = "Entry/exit structure or numeric predicate is not fully determined."
    return {
        "source_identity": row["source_identity"],
        "strategy_name": row["strategy_name"],
        "current_status": "REMAINS_UNRESOLVED",
        "current_blockers": reason,
        "semantic_definition_complete": str(semantic_complete).lower(),
        "compiler_expression_complete": str(compiler_complete).lower(),
        "state_machine_complete": str(state_complete).lower(),
        "required_data_available": str("DATA_OR_FEATURE_CONTRACT_UNAVAILABLE" not in gaps).lower(),
        "compiler_gap_category": category,
        "semantic_gap_category": sem_category,
        "recoverable_by_compiler_extension": str(recoverable).lower(),
        "reason": reason_text,
        "required_new_ast_nodes": ";".join(sorted(ast)),
        "required_new_state_primitives": ";".join(sorted(state)),
        "required_new_timeframe_primitives": ";".join(sorted(timeframe)),
    }


def main() -> int:
    source = [row for row in read_csv(SOURCE) if row["phase5a_status"] == "REMAINS_UNRESOLVED"]
    if len(source) != 1082:
        raise SystemExit(f"expected 1082 unresolved Phase 5A rows, found {len(source)}")
    rows = [classify(row) for row in source]
    if len({row["source_identity"] for row in rows}) != len(rows):
        raise SystemExit("duplicate source identities in Phase 5B audit")
    write_csv(AUDIT / "phase5b_compiler_gap_audit.csv", rows, list(rows[0]))

    state_rows: list[dict[str, object]] = []
    for row in rows:
        if str(row["current_blockers"]).startswith("UNPARSEABLE_STATE_MACHINE"):
            state_rows.append({
                "source_identity": row["source_identity"],
                "strategy_name": row["strategy_name"],
                "state_pattern": row["compiler_gap_category"],
                "semantic_complete": row["semantic_definition_complete"],
                "required_state_primitives": row["required_new_state_primitives"],
                "required_actions": row["required_new_ast_nodes"],
                "remaining_semantic_gap": row["semantic_gap_category"],
            })
    if len(state_rows) != 218:
        raise SystemExit(f"expected 218 state-machine rows, found {len(state_rows)}")
    write_csv(AUDIT / "phase5b_state_machine_patterns.csv", state_rows, list(state_rows[0]))

    summary = {
        "schema_version": 1,
        "total_rows": len(rows),
        "unique_rows": len({row["source_identity"] for row in rows}),
        "recoverable_by_compiler_extension": sum(row["recoverable_by_compiler_extension"] == "true" for row in rows),
        "semantic_gap_rows": sum(row["semantic_gap_category"] != "NONE" for row in rows),
        "compiler_gap_categories": Counter(str(row["compiler_gap_category"]) for row in rows),
        "semantic_gap_categories": Counter(str(row["semantic_gap_category"]) for row in rows),
        "source_category_counts": Counter(
            "ENTRY_SIDE" if row["current_blockers"] == "ENTRY_SIDE_NOT_FULLY_REPRESENTABLE"
            else "MTF_SESSION" if row["current_blockers"] == "COMPLETED_MULTI_TIMEFRAME_OR_SESSION_STATE_NOT_FULLY_PARSEABLE"
            else "STATE_MACHINE" for row in rows
        ),
    }
    target = AUDIT / "phase5b_gap_audit_summary.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=dict) + "\n", encoding="utf-8")
    os.replace(temp, target)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=dict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
