"""Normal StrategyPlugin registration seam for xlsx_s2_0288."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0288.config import XlsxS20288Config
from strategies.xlsx_s2_0288.strategy import XlsxS20288Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0288",
    config_cls=XlsxS20288Config,
    strategy_cls=XlsxS20288Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0288/config.yaml",
)
