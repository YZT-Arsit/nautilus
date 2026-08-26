"""Normal StrategyPlugin registration seam for xlsx_s2_0539."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0539.config import XlsxS20539Config
from strategies.xlsx_s2_0539.strategy import XlsxS20539Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0539",
    config_cls=XlsxS20539Config,
    strategy_cls=XlsxS20539Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0539/config.yaml",
)
