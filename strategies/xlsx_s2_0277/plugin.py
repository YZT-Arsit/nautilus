"""Normal StrategyPlugin registration seam for xlsx_s2_0277."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0277.config import XlsxS20277Config
from strategies.xlsx_s2_0277.strategy import XlsxS20277Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0277",
    config_cls=XlsxS20277Config,
    strategy_cls=XlsxS20277Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0277/config.yaml",
)
