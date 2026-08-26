"""Normal StrategyPlugin registration seam for xlsx_s1_0504."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0504.config import XlsxS10504Config
from strategies.xlsx_s1_0504.strategy import XlsxS10504Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0504",
    config_cls=XlsxS10504Config,
    strategy_cls=XlsxS10504Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0504/config.yaml",
)
