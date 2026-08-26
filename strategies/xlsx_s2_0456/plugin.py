"""Normal StrategyPlugin registration seam for xlsx_s2_0456."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0456.config import XlsxS20456Config
from strategies.xlsx_s2_0456.strategy import XlsxS20456Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0456",
    config_cls=XlsxS20456Config,
    strategy_cls=XlsxS20456Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0456/config.yaml",
)
