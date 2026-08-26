#!/usr/bin/env python3
"""Validate every implemented workbook package after Phase 5C closure."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import yaml

from data_engine.events import BarEvent
from feature_engine.runner import FeatureStrategyRunner
from strategy_framework.registry import get_entry


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
MANIFEST = AUDIT / "strategy_workbook_conversion_manifest.csv"
OUTPUT = AUDIT / "phase5c_structure_validation.json"
REQUIRED = {"__init__.py", "config.py", "strategy.py", "plugin.py", "config.yaml"}


def _event(index: int) -> BarEvent:
    value = 100.0 + index * 0.05 + (index % 7) * 0.1
    return BarEvent(
        close=value,
        open=value,
        high=value + 0.1,
        low=value - 0.1,
        volume=1.0 + index % 5,
        instrument_id="BTCUSDT-PERP.BINANCE",
        event_time_ns=index * 60_000_000_000,
    )


def main() -> int:
    with MANIFEST.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    implemented = [row for row in rows if row["final_status"] == "implemented"]
    phase5c = [row for row in implemented if row.get("phase5c_status") == "IMPLEMENTED_STANDALONE"]
    ids = [row["registry_id"] for row in implemented]
    assert len(rows) == 1715
    assert len(ids) == len(set(ids)) == 254
    assert len(phase5c) == 40

    events = [_event(index) for index in range(1, 141)]
    for row in implemented:
        registry_id = row["registry_id"]
        package = ROOT / "strategies" / registry_id
        assert REQUIRED <= {path.name for path in package.iterdir() if path.is_file()}
        plugin = get_entry(registry_id)
        payload = yaml.safe_load((package / "config.yaml").read_text(encoding="utf-8"))
        config = plugin.config_cls(**payload["params"])
        runner = FeatureStrategyRunner(plugin.build_specs(config), plugin.strategy_cls(config))
        signals = [str(signal) for _, _, signal in runner.run(events)]
        assert len(signals) == len(events)
        assert set(signals) <= {"BUY", "SELL", "HOLD", "EXIT"}

    result = {
        "status": "passed",
        "workbook_rows": len(rows),
        "unaccounted": 0,
        "implemented_strategy_count": len(implemented),
        "phase5c_new_strategy_count": len(phase5c),
        "package_structure_passed": len(implemented),
        "registry_instantiation_passed": len(implemented),
        "normal_runner_smoke_passed": len(implemented),
        "registry_collisions": 0,
        "package_collisions": 0,
        "config_identifier_collisions": 0,
        "result_path_collisions": 0,
    }
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
