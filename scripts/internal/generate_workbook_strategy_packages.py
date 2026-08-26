#!/usr/bin/env python3
"""Generate thin first-class packages for reviewed workbook strategies.

This is repository build tooling. Generated strategies never read the source
workbook: parameters and provenance are compiled into Python/YAML files.
"""

from __future__ import annotations

import argparse
from math import isqrt
from pathlib import Path
import sys
import json

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from scripts.internal.audit_strategy_workbook import IMPLEMENTED

FILL_AWARE_FAMILIES = {
    "donchian_ma_stop", "adx_donchian_stop", "adx_sma_take_profit",
    "ema_adx_take_profit", "supertrend_stop", "donchian_stop",
    "ma_cross_slope_atr_exit",
    "psar_atr_distance_exit",
    "donchian_pyramid",
}


def class_name(registry_id: str) -> str:
    return "".join(part.capitalize() for part in registry_id.split("_"))


def yaml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(registry_id: str, definition: dict[str, object]) -> dict[str, str]:
    cls = class_name(registry_id)
    family = str(definition["family"])
    params = dict(definition["params"])
    provenance = str(definition.get("semantic_provenance", "SOURCE_EXACT"))
    contracts = ";".join(str(item) for item in definition.get("contracts_applied", []))
    defaulted = ";".join(
        f"{name}={value}"
        for name, value in dict(definition.get("defaulted_parameters", {})).items()
    )
    defaults = {
        "source_registry_id": registry_id,
        "family": family,
        "semantic_provenance": provenance,
        "contracts_applied": contracts,
        "defaulted_parameters": defaulted,
        **params,
    }
    window_values = [int(value) for name, value in params.items() if "window" in name]
    warmup_bars = max(window_values, default=2)
    if family == "hma_turn":
        warmup_bars = int(params["window"]) + isqrt(int(params["window"])) - 1
    elif family == "psar_reversal":
        warmup_bars = 2
    fields = "\n".join(f"    {name}: {type(value).__name__} = {value!r}" for name, value in defaults.items())
    yaml_params = "\n".join(f"  {name}: {yaml_value(value)}" for name, value in defaults.items())
    base_strategy = "WorkbookExecutionAdapter" if family in FILL_AWARE_FAMILIES else "WorkbookParametricStrategy"
    base_module = "strategies.workbook_parametric.execution_adapter" if family in FILL_AWARE_FAMILIES else "strategies.workbook_parametric.strategy"
    return {
        "__init__.py": f'''"""Workbook strategy {registry_id}; source provenance is in config.yaml."""\n\nfrom strategies.{registry_id}.config import {cls}Config\nfrom strategies.{registry_id}.plugin import PLUGIN\nfrom strategies.{registry_id}.strategy import {cls}Strategy\n\n__all__ = ["PLUGIN", "{cls}Config", "{cls}Strategy"]\n''',
        "config.py": f'''"""Typed configuration compiled from workbook row {registry_id}."""\n\nfrom dataclasses import dataclass\n\nfrom strategies.workbook_parametric.config import WorkbookParametricConfig\n\n\n@dataclass(frozen=True)\nclass {cls}Config(WorkbookParametricConfig):\n{fields}\n''',
        "strategy.py": f'''"""Normal FeatureSnapshot strategy adapter for {registry_id}."""\n\nfrom {base_module} import {base_strategy}\n\n\nclass {cls}Strategy({base_strategy}):\n    """Reviewed row-specific type; mechanics are shared by exact family."""\n''',
        "plugin.py": f'''"""Normal StrategyPlugin registration seam for {registry_id}."""\n\nfrom strategy_framework.plugin import StrategyPlugin\nfrom strategies.workbook_parametric.plugin import build_specs\nfrom strategies.{registry_id}.config import {cls}Config\nfrom strategies.{registry_id}.strategy import {cls}Strategy\n\nPLUGIN = StrategyPlugin(\n    name="{registry_id}",\n    config_cls={cls}Config,\n    strategy_cls={cls}Strategy,\n    build_specs=build_specs,\n    default_config_path="strategies/{registry_id}/config.yaml",\n)\n''',
        "config.yaml": f'''strategy: {registry_id}\nparams:\n{yaml_params}\ndata:\n  mode: synthetic\n  instrument_id: BTCUSDT-PERP.BINANCE\n  warmup_bars: {warmup_bars}\n  live_bars: 120\noutput:\n  print_table: false\n  record_signals: true\nprovenance:\n  workbook: 时序策略.xlsx\n  registry_id: {registry_id}\n''',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--plan", type=Path,
        help="Generate/check only strategy IDs from this compiled JSON plan.",
    )
    args = parser.parse_args()
    mismatches: list[str] = []
    selected = IMPLEMENTED
    if args.plan:
        selected = json.loads(args.plan.read_text(encoding="utf-8"))
    for registry_id, definition in sorted(selected.items()):
        package = args.root / "strategies" / registry_id
        for filename, content in render(registry_id, definition).items():
            path = package / filename
            if args.check:
                if not path.is_file() or path.read_text(encoding="utf-8") != content:
                    mismatches.append(str(path))
                continue
            package.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
    if mismatches:
        print("\n".join(mismatches))
        return 1
    print(f"workbook strategy packages: {len(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
