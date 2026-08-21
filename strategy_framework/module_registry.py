"""Explicit registry for composable strategy modules."""
from __future__ import annotations

from strategy_framework.modules import StrategyModule
from strategy_framework.modules import (
    AdxExposureModule, AtrHardStopModule, AtrLadderExitModule, DonchianExitModule,
)

import json
from pathlib import Path


MODULE_REGISTRY: dict[str, StrategyModule] = {}


def register_module(module: StrategyModule) -> None:
    if module.module_id in MODULE_REGISTRY:
        raise ValueError(f"duplicate strategy module: {module.module_id}")
    MODULE_REGISTRY[module.module_id] = module


def get_module(module_id: str) -> StrategyModule:
    try:
        return MODULE_REGISTRY[module_id]
    except KeyError:
        raise KeyError(f"unknown strategy module {module_id!r}") from None


def load_module_configs(path: str | Path) -> int:
    """Load deterministic compiled module configs; never reads the workbook."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in payload:
        module_type = row["module_type"]
        if module_type == "atr_ladder_exit":
            module = AtrLadderExitModule(
                module_id=row["module_id"],
                profit_levels_atr=tuple(float(value) for value in row["profit_levels_atr"]),
                reduction_fractions=tuple(float(value) for value in row["reduction_fractions"]),
                final_profit_atr=float(row["final_profit_atr"]),
                stop_loss_atr=float(row["stop_loss_atr"]),
            )
        elif module_type == "atr_hard_stop":
            module = AtrHardStopModule(row["module_id"], float(row["stop_loss_atr"]))
        elif module_type == "donchian_exit":
            module = DonchianExitModule(row["module_id"], int(row["window"]))
        elif module_type == "adx_exposure":
            module = AdxExposureModule(
                row["module_id"], float(row["full_threshold"]),
                float(row["medium_threshold"]), float(row["medium_exposure"]),
                float(row["low_exposure"]),
            )
        else:
            raise ValueError(f"unsupported strategy module type: {module_type}")
        register_module(module)
    return len(payload)
