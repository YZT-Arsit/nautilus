"""Normal StrategyPlugin registration seam for xlsx_s2_0125."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0125.config import XlsxS20125Config
from strategies.xlsx_s2_0125.strategy import XlsxS20125Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0125",
    config_cls=XlsxS20125Config,
    strategy_cls=XlsxS20125Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0125/config.yaml",
)
