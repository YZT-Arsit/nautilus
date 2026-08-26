"""Normal StrategyPlugin registration seam for xlsx_s2_0592."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0592.config import XlsxS20592Config
from strategies.xlsx_s2_0592.strategy import XlsxS20592Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0592",
    config_cls=XlsxS20592Config,
    strategy_cls=XlsxS20592Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0592/config.yaml",
)
