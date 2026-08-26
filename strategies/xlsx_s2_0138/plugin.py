"""Normal StrategyPlugin registration seam for xlsx_s2_0138."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0138.config import XlsxS20138Config
from strategies.xlsx_s2_0138.strategy import XlsxS20138Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0138",
    config_cls=XlsxS20138Config,
    strategy_cls=XlsxS20138Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0138/config.yaml",
)
