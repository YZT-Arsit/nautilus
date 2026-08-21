"""Normal StrategyPlugin registration seam for xlsx_s1_0002."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0002.config import XlsxS10002Config
from strategies.xlsx_s1_0002.strategy import XlsxS10002Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0002",
    config_cls=XlsxS10002Config,
    strategy_cls=XlsxS10002Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0002/config.yaml",
)
