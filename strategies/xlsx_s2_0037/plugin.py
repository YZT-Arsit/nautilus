"""Normal StrategyPlugin registration seam for xlsx_s2_0037."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0037.config import XlsxS20037Config
from strategies.xlsx_s2_0037.strategy import XlsxS20037Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0037",
    config_cls=XlsxS20037Config,
    strategy_cls=XlsxS20037Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0037/config.yaml",
)
