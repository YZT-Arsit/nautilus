"""Normal StrategyPlugin registration seam for xlsx_s1_0441."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0441.config import XlsxS10441Config
from strategies.xlsx_s1_0441.strategy import XlsxS10441Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0441",
    config_cls=XlsxS10441Config,
    strategy_cls=XlsxS10441Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0441/config.yaml",
)
