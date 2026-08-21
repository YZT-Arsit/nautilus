"""Normal StrategyPlugin registration seam for xlsx_s2_0253."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0253.config import XlsxS20253Config
from strategies.xlsx_s2_0253.strategy import XlsxS20253Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0253",
    config_cls=XlsxS20253Config,
    strategy_cls=XlsxS20253Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0253/config.yaml",
)
