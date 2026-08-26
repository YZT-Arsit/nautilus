"""Normal StrategyPlugin registration seam for xlsx_s2_0840."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0840.config import XlsxS20840Config
from strategies.xlsx_s2_0840.strategy import XlsxS20840Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0840",
    config_cls=XlsxS20840Config,
    strategy_cls=XlsxS20840Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0840/config.yaml",
)
