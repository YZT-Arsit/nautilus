"""Normal StrategyPlugin registration seam for xlsx_s2_0870."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0870.config import XlsxS20870Config
from strategies.xlsx_s2_0870.strategy import XlsxS20870Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0870",
    config_cls=XlsxS20870Config,
    strategy_cls=XlsxS20870Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0870/config.yaml",
)
