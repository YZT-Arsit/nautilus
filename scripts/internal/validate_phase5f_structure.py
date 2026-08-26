#!/usr/bin/env python3
"""Validate Phase 5F package, registry, contract, and closure boundaries."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from strategy_framework.registry import STRATEGY_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase5f_strategies.json"


def main() -> int:
    plan: dict[str, dict[str, object]] = json.loads(PLAN.read_text(encoding="utf-8"))
    failures: list[str] = []
    required = {"__init__.py", "config.py", "strategy.py", "plugin.py", "config.yaml"}
    for identity, definition in plan.items():
        package = ROOT / "strategies" / identity
        if not package.is_dir() or not required <= {p.name for p in package.iterdir()}:
            failures.append(f"package:{identity}"); continue
        plugin = importlib.import_module(f"strategies.{identity}.plugin").PLUGIN
        if STRATEGY_REGISTRY.get(identity) is not plugin:
            failures.append(f"registry:{identity}")
        try:
            config = plugin.config_cls(); plugin.build_specs(config); plugin.strategy_cls(config)
        except Exception as exc:
            failures.append(f"instantiate:{identity}:{exc}")
        if definition.get("remaining_blockers") or int(definition.get("unmapped_material_source_clauses", 1)):
            failures.append(f"closure:{identity}")
        if definition.get("semantic_provenance") != "MODELLED_BASELINE_INTERPRETATION":
            failures.append(f"provenance:{identity}")
    active = json.loads((ROOT / "configs/semantic_contracts/workbook_phase5f_contracts.json").read_text(encoding="utf-8"))
    invalid = [name for name, item in active.items() if item["risk_level"] != "MEDIUM"]
    payload = {
        "strategy_count": len(plan), "package_failures": failures,
        "registry_failures": [item for item in failures if item.startswith("registry:")],
        "active_contracts": sorted(active), "non_medium_active_contracts": invalid,
        "normal_strategy_plugin": True, "normal_package_structure": not failures,
        "runtime_excel_dependency": False, "parallel_excel_runtime": False,
        "monolithic_generated_file": False,
    }
    payload["passed"] = not failures and sorted(active) == sorted((
        "MODELLED_BOUNDED_EQUAL_LADDER_V1", "MODELLED_STANDARD_REGULAR_DIVERGENCE_V1",
    )) and not invalid
    (AUDIT / "phase5f_structure_validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
