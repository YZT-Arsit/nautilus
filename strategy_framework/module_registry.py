"""Explicit registry for composable strategy modules."""

from __future__ import annotations

import json
from pathlib import Path

from strategy_framework.modules import AccountDrawdownControlModule
from strategy_framework.modules import AdxExposureModule
from strategy_framework.modules import AtrAdverseReductionModule
from strategy_framework.modules import AtrBreakevenTrailingModule
from strategy_framework.modules import AtrHardStopModule
from strategy_framework.modules import AtrLadderExitModule
from strategy_framework.modules import AtrTakeProfitModule
from strategy_framework.modules import CompositeRiskModule
from strategy_framework.modules import DailyRiskControlModule
from strategy_framework.modules import DonchianExitModule
from strategy_framework.modules import EntryExposureCapModule
from strategy_framework.modules import ExposureCapModule
from strategy_framework.modules import FeatureExitCondition
from strategy_framework.modules import FeatureExitModule
from strategy_framework.modules import FeatureExposureModule
from strategy_framework.modules import FixedPercentageStopModule
from strategy_framework.modules import StrategyModule
from strategy_framework.modules import TimeExitModule
from strategy_framework.modules import VolatilityExposureModule


MODULE_REGISTRY: dict[str, StrategyModule] = {}
MODULE_METADATA: dict[str, dict[str, object]] = {}


def register_module(module: StrategyModule, metadata: dict[str, object] | None = None) -> None:
    if module.module_id in MODULE_REGISTRY:
        raise ValueError(f"duplicate strategy module: {module.module_id}")
    MODULE_REGISTRY[module.module_id] = module
    MODULE_METADATA[module.module_id] = dict(metadata or {})


def get_module(module_id: str) -> StrategyModule:
    try:
        return MODULE_REGISTRY[module_id]
    except KeyError:
        raise KeyError(f"unknown strategy module {module_id!r}") from None


def _build_module(row: dict[str, object]) -> StrategyModule:
    module_type = row["module_type"]
    if module_type == "composite_risk":
        return CompositeRiskModule(
            str(row["module_id"]),
            tuple(_build_module(dict(item)) for item in row["modules"]),
        )
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
            row["module_id"],
            float(row["full_threshold"]),
            float(row["medium_threshold"]),
            float(row["medium_exposure"]),
            float(row["low_exposure"]),
        )
    elif module_type == "fixed_percentage_stop":
        module = FixedPercentageStopModule(row["module_id"], float(row["stop_fraction"]))
    elif module_type == "time_exit":
        module = TimeExitModule(row["module_id"], int(row["maximum_holding_bars"]))
    elif module_type == "exposure_cap":
        module = ExposureCapModule(row["module_id"], float(row["max_abs_exposure"]))
    elif module_type == "entry_exposure_cap":
        module = EntryExposureCapModule(row["module_id"], float(row["max_entry_exposure"]))
    elif module_type == "volatility_exposure":
        module = VolatilityExposureModule(
            row["module_id"],
            tuple(float(x) for x in row["upper_bounds"]),
            tuple(float(x) for x in row["exposures"]),
            float(row["prohibit_entry_at_or_above"])
            if row.get("prohibit_entry_at_or_above") is not None
            else None,
        )
    elif module_type == "atr_breakeven_trailing":
        module = AtrBreakevenTrailingModule(
            row["module_id"],
            float(row["activation_atr"]),
            float(row["lock_atr"]),
            float(row["hard_stop_atr"]),
            float(row["trail_distance_atr"]) if row.get("trail_distance_atr") is not None else None,
        )
    elif module_type == "account_drawdown":
        module = AccountDrawdownControlModule(
            row["module_id"],
            float(row["reduce_at"]),
            float(row["flatten_at"]),
            float(row.get("reduced_exposure", 0.5)),
        )
    elif module_type == "atr_take_profit":
        module = AtrTakeProfitModule(
            row["module_id"],
            float(row["take_profit_atr"]),
            float(row["stop_loss_atr"]) if row.get("stop_loss_atr") is not None else None,
        )
    elif module_type == "atr_adverse_reduction":
        module = AtrAdverseReductionModule(
            row["module_id"],
            tuple(float(x) for x in row["loss_levels_atr"]),
            tuple(float(x) for x in row["target_fractions"]),
        )
    elif module_type == "feature_exit":
        module = FeatureExitModule(
            row["module_id"],
            tuple(
                FeatureExitCondition(
                    feature_key=item["feature_key"],
                    operator=item.get("operator", "true"),
                    threshold=item.get("threshold"),
                    side=item.get("side", "both"),
                )
                for item in row["conditions"]
            ),
        )
    elif module_type == "feature_exposure":
        item = row["condition"]
        module = FeatureExposureModule(
            row["module_id"],
            FeatureExitCondition(
                feature_key=item["feature_key"],
                operator=item.get("operator", "true"),
                threshold=item.get("threshold"),
                side=item.get("side", "both"),
            ),
            float(row["target_fraction"]),
        )
    elif module_type == "daily_risk":
        module = DailyRiskControlModule(
            row["module_id"],
            float(row["maximum_loss"]) if row.get("maximum_loss") is not None else None,
            int(row["maximum_entries"]) if row.get("maximum_entries") is not None else None,
        )
    else:
        raise ValueError(f"unsupported strategy module type: {module_type}")
    return module


def load_module_configs(path: str | Path) -> int:
    """Load deterministic compiled module configs; never reads the workbook."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in payload:
        module = _build_module(row)
        metadata = {
            key: row.get(key)
            for key in (
                "module_family",
                "semantic_provenance",
                "contracts_applied",
                "defaulted_parameters",
                "source_identity",
                "source_sheet",
                "source_strategy_number",
                "source_strategy_name",
                "module_version",
            )
            if key in row
        }
        register_module(module, metadata)
    return len(payload)
