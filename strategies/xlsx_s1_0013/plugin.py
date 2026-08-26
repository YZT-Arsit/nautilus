"""Normal StrategyPlugin registration seam for xlsx_s1_0013."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0013.config import XlsxS10013Config
from strategies.xlsx_s1_0013.strategy import XlsxS10013Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0013",
    config_cls=XlsxS10013Config,
    strategy_cls=XlsxS10013Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0013/config.yaml",
)
