"""Normal StrategyPlugin registration seam for xlsx_s2_0205."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0205.config import XlsxS20205Config
from strategies.xlsx_s2_0205.strategy import XlsxS20205Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0205",
    config_cls=XlsxS20205Config,
    strategy_cls=XlsxS20205Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0205/config.yaml",
)
