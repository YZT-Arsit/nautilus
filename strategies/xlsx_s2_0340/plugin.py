"""Normal StrategyPlugin registration seam for xlsx_s2_0340."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0340.config import XlsxS20340Config
from strategies.xlsx_s2_0340.strategy import XlsxS20340Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0340",
    config_cls=XlsxS20340Config,
    strategy_cls=XlsxS20340Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0340/config.yaml",
)
