"""Normal StrategyPlugin registration seam for xlsx_s1_0514."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0514.config import XlsxS10514Config
from strategies.xlsx_s1_0514.strategy import XlsxS10514Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0514",
    config_cls=XlsxS10514Config,
    strategy_cls=XlsxS10514Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0514/config.yaml",
)
