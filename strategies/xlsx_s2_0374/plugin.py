"""Normal StrategyPlugin registration seam for xlsx_s2_0374."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0374.config import XlsxS20374Config
from strategies.xlsx_s2_0374.strategy import XlsxS20374Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0374",
    config_cls=XlsxS20374Config,
    strategy_cls=XlsxS20374Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0374/config.yaml",
)
