"""Normal StrategyPlugin registration seam for xlsx_s2_0836."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0836.config import XlsxS20836Config
from strategies.xlsx_s2_0836.strategy import XlsxS20836Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0836",
    config_cls=XlsxS20836Config,
    strategy_cls=XlsxS20836Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0836/config.yaml",
)
