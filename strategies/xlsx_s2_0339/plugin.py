"""Normal StrategyPlugin registration seam for xlsx_s2_0339."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0339.config import XlsxS20339Config
from strategies.xlsx_s2_0339.strategy import XlsxS20339Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0339",
    config_cls=XlsxS20339Config,
    strategy_cls=XlsxS20339Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0339/config.yaml",
)
