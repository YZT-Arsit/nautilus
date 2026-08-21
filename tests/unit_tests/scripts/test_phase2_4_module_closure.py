import csv
import json
from collections import Counter
from pathlib import Path

from strategy_framework.module_registry import MODULE_METADATA
from strategy_framework.module_registry import MODULE_REGISTRY
from strategy_framework.module_registry import load_module_configs


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def read_csv(name: str):
    with (AUDIT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_all_217_rows_have_one_terminal_disposition() -> None:
    rows = read_csv("phase2_4_module_closure.csv")
    assert len(rows) == 217
    assert len({row["source_identity"] for row in rows}) == 217
    allowed = {
        "IMPLEMENTED_MODULE_EXACT",
        "IMPLEMENTED_MODULE_FAMILY",
        "IMPLEMENTED_MODULE_DEFAULTED",
        "BLOCKED_MODULE_MISSING_PARAMETER",
        "BLOCKED_MODULE_AMBIGUOUS_SEMANTICS",
        "BLOCKED_MODULE_MISSING_DATA",
        "BLOCKED_MODULE_ENGINE_SCOPE",
    }
    assert set(row["new_status"] for row in rows) <= allowed
    assert all(row["remaining_blockers"] for row in rows if row["new_status"].startswith("BLOCKED"))
    assert all(
        not row["remaining_blockers"] for row in rows if row["new_status"].startswith("IMPLEMENTED")
    )


def test_compiled_config_and_registry_manifest_are_collision_free() -> None:
    configs = json.loads(
        (ROOT / "configs/strategy_modules/workbook_phase2_4_modules.json").read_text(
            encoding="utf-8"
        )
    )
    ids = [row["module_id"] for row in configs]
    assert len(ids) == len(set(ids))
    closure = read_csv("phase2_4_module_closure.csv")
    expected = {
        row["source_identity"] for row in closure if row["new_status"].startswith("IMPLEMENTED")
    }
    assert set(ids) == expected
    MODULE_REGISTRY.clear()
    MODULE_METADATA.clear()
    assert load_module_configs(ROOT / "configs/strategy_modules/workbook_atr_ladders.json") == 36
    assert load_module_configs(
        ROOT / "configs/strategy_modules/workbook_phase2_4_modules.json"
    ) == len(expected)
    assert len(MODULE_REGISTRY) == 36 + len(expected)
    assert all(MODULE_METADATA[module_id]["source_identity"] == module_id for module_id in expected)


def test_full_workbook_reconciliation_remains_exact() -> None:
    summary = json.loads((AUDIT / "phase2_4_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["module_rows_start"] == 217
    assert sum(summary["status_counts"].values()) == 217
    assert sum(summary["full_workbook_reconciliation"].values()) == 1715
    assert summary["optimization_executed"] == 0
    manifest = read_csv("strategy_workbook_conversion_manifest.csv")
    assert len(manifest) == 1715
    assert Counter(row["phase2_4_status"] for row in manifest)["UNCHANGED"] == 1715 - 217
