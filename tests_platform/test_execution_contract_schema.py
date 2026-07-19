"""Schema-level checks for the stabilized execution migration contract."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from strategy_framework.execution.legacy_adapter import LegacyExecutionState
from strategy_framework.execution.reports import FillRecord


_ROOT = Path(__file__).resolve().parents[1]


def _rows(name: str):
    with (_ROOT / name).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


def test_legacy_adapter_interface_is_fill_synchronized():
    state = LegacyExecutionState("BTCUSDT-PERP.BINANCE", {"BUY": 1, "SELL": 0})
    state.observe_signal("BUY")
    assert state.pending_target_position == 1
    assert state.position == 0
    assert callable(state.on_fill)
    assert callable(state.on_execution_report)

    state.on_fill(FillRecord("BTCUSDT-PERP.BINANCE", "BUY", 0.001, 100.0, 1))
    assert state.position == 1
    assert state.entry_fill_price == 100.0


def test_migration_registry_schema_and_status_values():
    fields, rows = _rows("strategy_execution_migration_registry.csv")
    assert fields == [
        "strategy_name", "strategy_type", "execution_status", "adapter_type",
        "signal_contract", "position_contract", "fill_contract", "fee_supported",
        "funding_supported", "latency_supported", "five_year_validated",
        "migration_version", "migration_date", "state_complexity",
        "migration_pattern", "execution_dependency", "estimated_effort",
    ]
    assert len(rows) == 66
    assert {row["execution_status"] for row in rows} <= {
        "compatible", "migrated", "needs_refactor", "blocked",
    }
    assert sum(row["execution_status"] == "compatible" for row in rows) == 2
    assert sum(row["execution_status"] == "migrated" for row in rows) == 62


def test_strategy_priority_schema_and_categories():
    fields, rows = _rows("strategy_refactor_priority.csv")
    assert fields == [
        "strategy", "current_status", "category", "execution_issue",
        "complexity", "risk", "priority",
    ]
    assert len(rows) == 60
    assert {row["current_status"] for row in rows} <= {"needs_refactor", "blocked"}
    assert {row["category"] for row in rows} <= {
        "A_simple_adapter", "B_state_migration", "C_complex", "D_blocked",
    }


def test_strategy_migration_pattern_mapping_schema_and_coverage():
    fields, rows = _rows("strategy_migration_pattern_mapping.csv")
    assert fields == [
        "strategy", "current_status", "migration_pattern", "state_complexity",
        "execution_dependency", "requires_new_pattern", "priority", "notes",
    ]
    assert len(rows) == 59
    assert sum(row["current_status"] == "needs_refactor" for row in rows) == 57
    assert sum(row["current_status"] == "blocked" for row in rows) == 2
    assert {row["migration_pattern"] for row in rows} <= {
        "position_gate", "fill_anchored_price", "market_derived_stop_target",
        "filled_position_lifecycle", "virtual_trade_decision_model",
        "pyramid_fill_reconciliation", "blocked", "unknown",
    }
    assert {row["requires_new_pattern"] for row in rows} == {"false"}


def test_migration_pattern_coverage_schema():
    path = _ROOT / "migration_pattern_coverage.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == 8
    assert all(set(row) == {
        "pattern", "strategy_count", "examples", "coverage_status", "notes",
    } for row in rows)
    assert sum(
        row["strategy_count"] for row in rows
        if row["pattern"] in {
            "position_gate", "fill_anchored_price", "market_derived_stop_target",
            "filled_position_lifecycle", "virtual_trade_decision_model",
        }
    ) == 57
    assert next(row for row in rows if row["pattern"] == "unknown")["strategy_count"] == 0
