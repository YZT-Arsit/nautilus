"""Normal StrategyPlugin registration seam for xlsx_s2_0453."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0453.config import XlsxS20453Config
from strategies.xlsx_s2_0453.strategy import XlsxS20453Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0453",
    config_cls=XlsxS20453Config,
    strategy_cls=XlsxS20453Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0453/config.yaml",
)
