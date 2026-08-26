"""Normal StrategyPlugin registration seam for xlsx_s2_0835."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0835.config import XlsxS20835Config
from strategies.xlsx_s2_0835.strategy import XlsxS20835Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0835",
    config_cls=XlsxS20835Config,
    strategy_cls=XlsxS20835Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0835/config.yaml",
)
