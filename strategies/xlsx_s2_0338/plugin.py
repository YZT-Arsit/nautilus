"""Normal StrategyPlugin registration seam for xlsx_s2_0338."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0338.config import XlsxS20338Config
from strategies.xlsx_s2_0338.strategy import XlsxS20338Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0338",
    config_cls=XlsxS20338Config,
    strategy_cls=XlsxS20338Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0338/config.yaml",
)
