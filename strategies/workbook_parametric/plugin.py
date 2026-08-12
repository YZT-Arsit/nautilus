from __future__ import annotations

from feature_engine.api import (
    FeatureSpec,
    atr_spec,
    bollinger_percent_b_spec,
    rolling_mean_spec,
)
from strategy_framework.plugin import StrategyPlugin

from strategies.workbook_parametric.config import WorkbookParametricConfig
from strategies.workbook_parametric.strategy import WorkbookParametricStrategy


DEFINITIONS = {
    "xlsx_s1_0002": "strategies/workbook_parametric/configs/xlsx_s1_0002.yaml",
    "xlsx_s1_0005": "strategies/workbook_parametric/configs/xlsx_s1_0005.yaml",
    "xlsx_s1_0010": "strategies/workbook_parametric/configs/xlsx_s1_0010.yaml",
    "xlsx_s1_0012": "strategies/workbook_parametric/configs/xlsx_s1_0012.yaml",
}


def build_specs(config: WorkbookParametricConfig) -> list[FeatureSpec]:
    common = [rolling_mean_spec("workbook_close", input_field="close", window=1)]
    if config.family == "sma_crossover":
        return common + [
            rolling_mean_spec("workbook_fast", window=config.fast_window),
            rolling_mean_spec("workbook_slow", window=config.slow_window),
        ]
    if config.family == "ma_envelope":
        return common + [rolling_mean_spec("workbook_middle", window=config.window)]
    if config.family == "bollinger":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            bollinger_percent_b_spec("workbook_percent_b", window=config.window, k=config.multiplier),
        ]
    if config.family == "atr_channel":
        return common + [
            rolling_mean_spec("workbook_middle", window=config.window),
            atr_spec("workbook_atr", window=config.atr_window),
        ]
    raise ValueError(f"unsupported exact workbook family: {config.family}")


PLUGINS = tuple(
    StrategyPlugin(
        name=registry_id,
        config_cls=WorkbookParametricConfig,
        strategy_cls=WorkbookParametricStrategy,
        build_specs=build_specs,
        default_config_path=config_path,
    )
    for registry_id, config_path in DEFINITIONS.items()
)
