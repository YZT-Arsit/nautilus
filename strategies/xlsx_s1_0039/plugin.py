"""Normal StrategyPlugin registration seam for xlsx_s1_0039."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0039.config import XlsxS10039Config
from strategies.xlsx_s1_0039.strategy import XlsxS10039Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0039",
    config_cls=XlsxS10039Config,
    strategy_cls=XlsxS10039Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0039/config.yaml",
)
