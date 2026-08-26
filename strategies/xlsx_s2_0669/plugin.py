"""Normal StrategyPlugin registration seam for xlsx_s2_0669."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0669.config import XlsxS20669Config
from strategies.xlsx_s2_0669.strategy import XlsxS20669Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0669",
    config_cls=XlsxS20669Config,
    strategy_cls=XlsxS20669Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0669/config.yaml",
)
