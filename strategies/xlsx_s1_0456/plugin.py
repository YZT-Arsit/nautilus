"""Normal StrategyPlugin registration seam for xlsx_s1_0456."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0456.config import XlsxS10456Config
from strategies.xlsx_s1_0456.strategy import XlsxS10456Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0456",
    config_cls=XlsxS10456Config,
    strategy_cls=XlsxS10456Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0456/config.yaml",
)
