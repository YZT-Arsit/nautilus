"""Normal StrategyPlugin registration seam for xlsx_s2_0127."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0127.config import XlsxS20127Config
from strategies.xlsx_s2_0127.strategy import XlsxS20127Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0127",
    config_cls=XlsxS20127Config,
    strategy_cls=XlsxS20127Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0127/config.yaml",
)
