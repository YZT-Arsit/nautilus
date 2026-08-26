"""Normal StrategyPlugin registration seam for xlsx_s2_0865."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0865.config import XlsxS20865Config
from strategies.xlsx_s2_0865.strategy import XlsxS20865Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0865",
    config_cls=XlsxS20865Config,
    strategy_cls=XlsxS20865Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0865/config.yaml",
)
