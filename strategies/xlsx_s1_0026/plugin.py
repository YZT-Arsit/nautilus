"""Normal StrategyPlugin registration seam for xlsx_s1_0026."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0026.config import XlsxS10026Config
from strategies.xlsx_s1_0026.strategy import XlsxS10026Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0026",
    config_cls=XlsxS10026Config,
    strategy_cls=XlsxS10026Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0026/config.yaml",
)
