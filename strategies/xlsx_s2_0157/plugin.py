"""Normal StrategyPlugin registration seam for xlsx_s2_0157."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0157.config import XlsxS20157Config
from strategies.xlsx_s2_0157.strategy import XlsxS20157Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0157",
    config_cls=XlsxS20157Config,
    strategy_cls=XlsxS20157Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0157/config.yaml",
)
