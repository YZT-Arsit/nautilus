"""Normal StrategyPlugin registration seam for xlsx_s2_0174."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0174.config import XlsxS20174Config
from strategies.xlsx_s2_0174.strategy import XlsxS20174Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0174",
    config_cls=XlsxS20174Config,
    strategy_cls=XlsxS20174Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0174/config.yaml",
)
