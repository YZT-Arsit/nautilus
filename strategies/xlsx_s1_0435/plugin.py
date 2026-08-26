"""Normal StrategyPlugin registration seam for xlsx_s1_0435."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0435.config import XlsxS10435Config
from strategies.xlsx_s1_0435.strategy import XlsxS10435Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0435",
    config_cls=XlsxS10435Config,
    strategy_cls=XlsxS10435Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0435/config.yaml",
)
