"""Normal StrategyPlugin registration seam for xlsx_s1_0023."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0023.config import XlsxS10023Config
from strategies.xlsx_s1_0023.strategy import XlsxS10023Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0023",
    config_cls=XlsxS10023Config,
    strategy_cls=XlsxS10023Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0023/config.yaml",
)
