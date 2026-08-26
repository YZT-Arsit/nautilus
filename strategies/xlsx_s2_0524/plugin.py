"""Normal StrategyPlugin registration seam for xlsx_s2_0524."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0524.config import XlsxS20524Config
from strategies.xlsx_s2_0524.strategy import XlsxS20524Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0524",
    config_cls=XlsxS20524Config,
    strategy_cls=XlsxS20524Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0524/config.yaml",
)
