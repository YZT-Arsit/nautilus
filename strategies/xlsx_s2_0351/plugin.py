"""Normal StrategyPlugin registration seam for xlsx_s2_0351."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0351.config import XlsxS20351Config
from strategies.xlsx_s2_0351.strategy import XlsxS20351Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0351",
    config_cls=XlsxS20351Config,
    strategy_cls=XlsxS20351Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0351/config.yaml",
)
