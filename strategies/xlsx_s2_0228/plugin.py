"""Normal StrategyPlugin registration seam for xlsx_s2_0228."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0228.config import XlsxS20228Config
from strategies.xlsx_s2_0228.strategy import XlsxS20228Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0228",
    config_cls=XlsxS20228Config,
    strategy_cls=XlsxS20228Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0228/config.yaml",
)
