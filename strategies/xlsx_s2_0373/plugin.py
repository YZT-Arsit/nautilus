"""Normal StrategyPlugin registration seam for xlsx_s2_0373."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0373.config import XlsxS20373Config
from strategies.xlsx_s2_0373.strategy import XlsxS20373Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0373",
    config_cls=XlsxS20373Config,
    strategy_cls=XlsxS20373Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0373/config.yaml",
)
