"""Normal StrategyPlugin registration seam for xlsx_s2_0534."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0534.config import XlsxS20534Config
from strategies.xlsx_s2_0534.strategy import XlsxS20534Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0534",
    config_cls=XlsxS20534Config,
    strategy_cls=XlsxS20534Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0534/config.yaml",
)
