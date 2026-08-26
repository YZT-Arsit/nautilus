"""Normal StrategyPlugin registration seam for xlsx_s2_0568."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0568.config import XlsxS20568Config
from strategies.xlsx_s2_0568.strategy import XlsxS20568Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0568",
    config_cls=XlsxS20568Config,
    strategy_cls=XlsxS20568Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0568/config.yaml",
)
