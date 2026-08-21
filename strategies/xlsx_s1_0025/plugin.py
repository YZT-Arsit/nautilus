"""Normal StrategyPlugin registration seam for xlsx_s1_0025."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0025.config import XlsxS10025Config
from strategies.xlsx_s1_0025.strategy import XlsxS10025Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0025",
    config_cls=XlsxS10025Config,
    strategy_cls=XlsxS10025Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0025/config.yaml",
)
