"""Normal StrategyPlugin registration seam for xlsx_s2_0438."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0438.config import XlsxS20438Config
from strategies.xlsx_s2_0438.strategy import XlsxS20438Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0438",
    config_cls=XlsxS20438Config,
    strategy_cls=XlsxS20438Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0438/config.yaml",
)
