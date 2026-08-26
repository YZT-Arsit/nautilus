"""Normal StrategyPlugin registration seam for xlsx_s1_0480."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0480.config import XlsxS10480Config
from strategies.xlsx_s1_0480.strategy import XlsxS10480Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0480",
    config_cls=XlsxS10480Config,
    strategy_cls=XlsxS10480Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0480/config.yaml",
)
