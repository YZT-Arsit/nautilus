"""Normal StrategyPlugin registration seam for xlsx_s2_0036."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0036.config import XlsxS20036Config
from strategies.xlsx_s2_0036.strategy import XlsxS20036Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0036",
    config_cls=XlsxS20036Config,
    strategy_cls=XlsxS20036Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0036/config.yaml",
)
