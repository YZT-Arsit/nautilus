"""Normal StrategyPlugin registration seam for xlsx_s2_0475."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0475.config import XlsxS20475Config
from strategies.xlsx_s2_0475.strategy import XlsxS20475Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0475",
    config_cls=XlsxS20475Config,
    strategy_cls=XlsxS20475Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0475/config.yaml",
)
