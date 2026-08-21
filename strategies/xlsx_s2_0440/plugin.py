"""Normal StrategyPlugin registration seam for xlsx_s2_0440."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0440.config import XlsxS20440Config
from strategies.xlsx_s2_0440.strategy import XlsxS20440Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0440",
    config_cls=XlsxS20440Config,
    strategy_cls=XlsxS20440Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0440/config.yaml",
)
