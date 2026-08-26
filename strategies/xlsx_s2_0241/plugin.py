"""Normal StrategyPlugin registration seam for xlsx_s2_0241."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0241.config import XlsxS20241Config
from strategies.xlsx_s2_0241.strategy import XlsxS20241Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0241",
    config_cls=XlsxS20241Config,
    strategy_cls=XlsxS20241Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0241/config.yaml",
)
