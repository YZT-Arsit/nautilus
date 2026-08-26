"""Normal StrategyPlugin registration seam for xlsx_s2_0469."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0469.config import XlsxS20469Config
from strategies.xlsx_s2_0469.strategy import XlsxS20469Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0469",
    config_cls=XlsxS20469Config,
    strategy_cls=XlsxS20469Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0469/config.yaml",
)
