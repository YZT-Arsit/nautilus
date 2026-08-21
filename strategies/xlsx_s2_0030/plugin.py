"""Normal StrategyPlugin registration seam for xlsx_s2_0030."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0030.config import XlsxS20030Config
from strategies.xlsx_s2_0030.strategy import XlsxS20030Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0030",
    config_cls=XlsxS20030Config,
    strategy_cls=XlsxS20030Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0030/config.yaml",
)
