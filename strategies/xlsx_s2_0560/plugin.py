"""Normal StrategyPlugin registration seam for xlsx_s2_0560."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0560.config import XlsxS20560Config
from strategies.xlsx_s2_0560.strategy import XlsxS20560Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0560",
    config_cls=XlsxS20560Config,
    strategy_cls=XlsxS20560Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0560/config.yaml",
)
