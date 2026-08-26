"""Normal StrategyPlugin registration seam for xlsx_s2_0615."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0615.config import XlsxS20615Config
from strategies.xlsx_s2_0615.strategy import XlsxS20615Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0615",
    config_cls=XlsxS20615Config,
    strategy_cls=XlsxS20615Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0615/config.yaml",
)
