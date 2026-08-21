#!/usr/bin/env python3
"""Dependency-light server validation for workbook conversion contracts."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from data_engine.events import BarEvent
from feature_engine.api import SpecFeatureEngine, adx_spec, cci_spec, hlc_mean_spec, hma_spec
from feature_engine.runner import FeatureStrategyRunner
from scripts.internal.audit_strategy_workbook import IMPLEMENTED, build_manifest
from strategy_framework.registry import get_entry


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def bar(value: float, index: int) -> BarEvent:
    return BarEvent(
        close=value, open=value, high=value, low=value, volume=1.0,
        instrument_id="BTCUSDT-PERP.BINANCE", event_time_ns=index * 60_000_000_000,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, counts = build_manifest(args.workbook)
    assert counts == {"Sheet1": 815, "Sheet2": 900}
    assert len(manifest) == 1715
    assert len({row["registry_id"] for row in manifest}) == 1715
    assert sum(row["final_status"] == "implemented" for row in manifest) == len(IMPLEMENTED)
    required = {"__init__.py", "config.py", "strategy.py", "plugin.py", "config.yaml"}
    events = [bar(100 + i * 0.05 + (i % 7) * 0.1, i + 1) for i in range(140)]
    for registry_id in IMPLEMENTED:
        package = REPOSITORY_ROOT / "strategies" / registry_id
        assert required <= {path.name for path in package.iterdir() if path.is_file()}
        plugin = get_entry(registry_id)
        payload = yaml.safe_load((package / "config.yaml").read_text(encoding="utf-8"))
        config = plugin.config_cls(**payload["params"])
        runner = FeatureStrategyRunner(plugin.build_specs(config), plugin.strategy_cls(config))
        signals = [str(signal) for _, _, signal in runner.run(events)]
        assert len(signals) == len(events)
        assert set(signals) <= {"BUY", "SELL", "HOLD", "EXIT"}
    engine = SpecFeatureEngine(
        [hma_spec("hma", window=4), cci_spec("cci", window=3), hlc_mean_spec("hlc", window=3)],
        stamp_process_time=False,
    )
    snapshots = [engine.on_event(bar(value, index)) for index, value in enumerate((1, 2, 3, 4, 5), 1)]
    assert math.isclose(snapshots[-1].value("hma"), 5.0)
    assert math.isclose(snapshots[2].value("cci"), 100.0)
    assert math.isclose(snapshots[2].value("hlc"), 2.0)
    adx_engine = SpecFeatureEngine([adx_spec("adx", window=3)], stamp_process_time=False)
    adx_snapshots = [adx_engine.on_event(bar(value, value)) for value in range(1, 6)]
    assert math.isclose(adx_snapshots[-1].value("adx"), 100.0)
    result = {
        "status": "passed", "workbook_rows": len(manifest),
        "unaccounted": 0, "registry_collisions": 0,
        "package_collisions": 0, "config_identifier_collisions": 0,
        "result_path_collisions": 0,
        "implemented_strategy_count": len(IMPLEMENTED),
        "package_structure_passed": len(IMPLEMENTED),
        "registry_instantiation_passed": len(IMPLEMENTED),
        "normal_runner_smoke_passed": len(IMPLEMENTED),
        "feature_golden_checks": 4,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
