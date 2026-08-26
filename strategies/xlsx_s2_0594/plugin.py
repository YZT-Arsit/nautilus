"""Normal StrategyPlugin registration seam for xlsx_s2_0594."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0594.config import XlsxS20594Config
from strategies.xlsx_s2_0594.strategy import XlsxS20594Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0594",
    config_cls=XlsxS20594Config,
    strategy_cls=XlsxS20594Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0594/config.yaml",
)
