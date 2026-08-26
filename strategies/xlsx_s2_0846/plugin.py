"""Normal StrategyPlugin registration seam for xlsx_s2_0846."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0846.config import XlsxS20846Config
from strategies.xlsx_s2_0846.strategy import XlsxS20846Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0846",
    config_cls=XlsxS20846Config,
    strategy_cls=XlsxS20846Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0846/config.yaml",
)
