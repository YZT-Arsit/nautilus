"""Normal StrategyPlugin registration seam for xlsx_s1_0029."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0029.config import XlsxS10029Config
from strategies.xlsx_s1_0029.strategy import XlsxS10029Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0029",
    config_cls=XlsxS10029Config,
    strategy_cls=XlsxS10029Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0029/config.yaml",
)
