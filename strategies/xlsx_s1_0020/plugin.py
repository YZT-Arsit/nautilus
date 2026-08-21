"""Normal StrategyPlugin registration seam for xlsx_s1_0020."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0020.config import XlsxS10020Config
from strategies.xlsx_s1_0020.strategy import XlsxS10020Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0020",
    config_cls=XlsxS10020Config,
    strategy_cls=XlsxS10020Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0020/config.yaml",
)
