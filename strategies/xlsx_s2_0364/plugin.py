"""Normal StrategyPlugin registration seam for xlsx_s2_0364."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0364.config import XlsxS20364Config
from strategies.xlsx_s2_0364.strategy import XlsxS20364Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0364",
    config_cls=XlsxS20364Config,
    strategy_cls=XlsxS20364Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0364/config.yaml",
)
