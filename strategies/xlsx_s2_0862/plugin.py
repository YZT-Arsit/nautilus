"""Normal StrategyPlugin registration seam for xlsx_s2_0862."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0862.config import XlsxS20862Config
from strategies.xlsx_s2_0862.strategy import XlsxS20862Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0862",
    config_cls=XlsxS20862Config,
    strategy_cls=XlsxS20862Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0862/config.yaml",
)
