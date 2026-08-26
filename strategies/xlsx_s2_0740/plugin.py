"""Normal StrategyPlugin registration seam for xlsx_s2_0740."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0740.config import XlsxS20740Config
from strategies.xlsx_s2_0740.strategy import XlsxS20740Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0740",
    config_cls=XlsxS20740Config,
    strategy_cls=XlsxS20740Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0740/config.yaml",
)
