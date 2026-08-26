"""Normal StrategyPlugin registration seam for xlsx_s2_0011."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0011.config import XlsxS20011Config
from strategies.xlsx_s2_0011.strategy import XlsxS20011Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0011",
    config_cls=XlsxS20011Config,
    strategy_cls=XlsxS20011Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0011/config.yaml",
)
