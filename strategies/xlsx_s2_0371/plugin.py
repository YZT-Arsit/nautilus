"""Normal StrategyPlugin registration seam for xlsx_s2_0371."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0371.config import XlsxS20371Config
from strategies.xlsx_s2_0371.strategy import XlsxS20371Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0371",
    config_cls=XlsxS20371Config,
    strategy_cls=XlsxS20371Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0371/config.yaml",
)
