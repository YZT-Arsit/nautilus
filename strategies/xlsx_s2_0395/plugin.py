"""Normal StrategyPlugin registration seam for xlsx_s2_0395."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0395.config import XlsxS20395Config
from strategies.xlsx_s2_0395.strategy import XlsxS20395Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0395",
    config_cls=XlsxS20395Config,
    strategy_cls=XlsxS20395Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0395/config.yaml",
)
