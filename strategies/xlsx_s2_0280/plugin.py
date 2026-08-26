"""Normal StrategyPlugin registration seam for xlsx_s2_0280."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0280.config import XlsxS20280Config
from strategies.xlsx_s2_0280.strategy import XlsxS20280Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0280",
    config_cls=XlsxS20280Config,
    strategy_cls=XlsxS20280Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0280/config.yaml",
)
