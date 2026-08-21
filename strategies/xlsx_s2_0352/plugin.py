"""Normal StrategyPlugin registration seam for xlsx_s2_0352."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0352.config import XlsxS20352Config
from strategies.xlsx_s2_0352.strategy import XlsxS20352Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0352",
    config_cls=XlsxS20352Config,
    strategy_cls=XlsxS20352Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0352/config.yaml",
)
