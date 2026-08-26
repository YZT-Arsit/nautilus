"""Normal StrategyPlugin registration seam for xlsx_s2_0164."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0164.config import XlsxS20164Config
from strategies.xlsx_s2_0164.strategy import XlsxS20164Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0164",
    config_cls=XlsxS20164Config,
    strategy_cls=XlsxS20164Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0164/config.yaml",
)
