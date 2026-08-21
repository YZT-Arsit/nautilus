import json
from pathlib import Path
import sys

from scripts.internal.analyze_semantic_blockers import (
    AMBIGUOUS_STATUSES,
    extract_row_blockers,
    standard_resolvable,
    strategy_blocker_sets,
    unlock_projection,
    main,
)


def source_row(**overrides):
    row = {
        "registry_id": "xlsx_s1_9999",
        "source_strategy_name": "test",
        "source_sheet": "Sheet1",
        "source_sheet_index": "1",
        "source_strategy_number": "9999",
        "source_indicator_definition": "MA20",
        "source_long_condition": "价格回踩 MA20 后企稳开多",
        "source_short_condition": "价格反弹至 MA20 后承压开空",
        "source_exit_condition": "反向交叉全部平仓",
        "source_timeframe_semantics": "bar_period",
        "phase2_1_status": "AMBIGUOUS_ENTRY_EXIT_LOGIC",
    }
    row.update(overrides)
    return row


def test_extracts_all_semantic_blockers_not_only_first() -> None:
    blockers = extract_row_blockers(source_row())
    ids = {row["normalized_blocker_id"] for row in blockers}
    assert {"PULLBACK_TO_LEVEL", "STABILIZE_AFTER_DECLINE", "REBOUND_TO_LEVEL", "REJECT_FROM_RESISTANCE"} <= ids


def test_explicit_fraction_and_persistence_are_not_marked_missing() -> None:
    blockers = extract_row_blockers(source_row(
        source_long_condition="连续 3 根收盘价高于 MA20 开多",
        source_short_condition="连续 3 根收盘价低于 MA20 开空",
        source_exit_condition="减仓 50% 后反向交叉全部平仓",
    ))
    ids = {row["normalized_blocker_id"] for row in blockers}
    assert "PERSISTENCE_COUNT_MISSING" not in ids
    assert "POSITION_FRACTION_MISSING" not in ids


def test_divergence_has_pivot_lookback_and_type_contracts() -> None:
    blockers = extract_row_blockers(source_row(
        source_long_condition="出现背离开多", source_short_condition="出现背离开空",
    ))
    ids = {row["normalized_blocker_id"] for row in blockers}
    assert {"DIVERGENCE_PIVOT_DEFINITION_MISSING", "DIVERGENCE_LOOKBACK_MISSING", "DIVERGENCE_TYPE_MISSING"} <= ids


def test_standard_ruleset_is_conservatively_detected() -> None:
    row = source_row(
        source_indicator_definition="EMA12 与 EMA26",
        source_long_condition="EMA12 上穿 EMA26 开多",
        source_short_condition="EMA12 下穿 EMA26 开空",
        source_exit_condition="反向交叉全部平仓",
    )
    blockers = extract_row_blockers(row)
    assert blockers == []
    assert standard_resolvable(row, blockers)


def test_projection_uses_set_union_and_never_sums_overlap() -> None:
    relationships = [
        {"source_identity": "a", "normalized_blocker_id": "A"},
        {"source_identity": "b", "normalized_blocker_id": "A"},
        {"source_identity": "b", "normalized_blocker_id": "B"},
    ]
    sets = strategy_blocker_sets(relationships)
    ranking = [
        {"contract_id": "A"}, {"contract_id": "B"},
    ]
    rows = unlock_projection(ranking, sets, current_executable=34)
    top1 = next(row for row in rows if row["scenario"] == "TOP_1_CONTRACT")
    top3 = next(row for row in rows if row["scenario"] == "TOP_3_CONTRACTS")
    assert top1["newly_unlockable_strategy_count"] == 1
    assert top3["newly_unlockable_strategy_count"] == 2
    assert top3["projected_total_executable_strategy_count"] == 36


def test_frozen_phase2_2a_analysis_reconciles_all_1196_rows() -> None:
    # The live conversion manifest changes as Phase 2.2B recovers strategies;
    # Phase 2.2A remains immutable under its canonical semantic audit root.
    summary = json.loads(Path(
        "outputs/internal_audit/strategy_workbook/semantic_contracts/semantic_analysis_summary.json"
    ).read_text(encoding="utf-8"))
    checks = summary["validations"]
    assert summary["ambiguous_strategy_count"] == 1196
    assert checks["ambiguous_with_blocker"] == 1196
    assert checks["implemented_rows_in_analysis"] == 0
    assert checks["session_rows_in_analysis"] == 0
    assert checks["missing_data_rows_in_analysis"] == 0
    assert checks["set_union_projection_valid"] is True
