"""Normal StrategyPlugin registration seam for xlsx_s2_0891."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0891.config import XlsxS20891Config
from strategies.xlsx_s2_0891.strategy import XlsxS20891Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0891",
    config_cls=XlsxS20891Config,
    strategy_cls=XlsxS20891Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0891/config.yaml",
)
