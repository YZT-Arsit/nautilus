"""Normal StrategyPlugin registration seam for xlsx_s1_0004."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0004.config import XlsxS10004Config
from strategies.xlsx_s1_0004.strategy import XlsxS10004Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0004",
    config_cls=XlsxS10004Config,
    strategy_cls=XlsxS10004Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0004/config.yaml",
)
