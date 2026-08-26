"""Normal StrategyPlugin registration seam for xlsx_s2_0480."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0480.config import XlsxS20480Config
from strategies.xlsx_s2_0480.strategy import XlsxS20480Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0480",
    config_cls=XlsxS20480Config,
    strategy_cls=XlsxS20480Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0480/config.yaml",
)
