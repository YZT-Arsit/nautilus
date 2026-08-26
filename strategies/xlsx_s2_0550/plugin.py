"""Normal StrategyPlugin registration seam for xlsx_s2_0550."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0550.config import XlsxS20550Config
from strategies.xlsx_s2_0550.strategy import XlsxS20550Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0550",
    config_cls=XlsxS20550Config,
    strategy_cls=XlsxS20550Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0550/config.yaml",
)
