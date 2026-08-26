"""Normal StrategyPlugin registration seam for xlsx_s2_0397."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0397.config import XlsxS20397Config
from strategies.xlsx_s2_0397.strategy import XlsxS20397Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0397",
    config_cls=XlsxS20397Config,
    strategy_cls=XlsxS20397Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0397/config.yaml",
)
