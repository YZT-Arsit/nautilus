"""Normal StrategyPlugin registration seam for xlsx_s2_0142."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0142.config import XlsxS20142Config
from strategies.xlsx_s2_0142.strategy import XlsxS20142Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0142",
    config_cls=XlsxS20142Config,
    strategy_cls=XlsxS20142Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0142/config.yaml",
)
