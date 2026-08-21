"""Normal StrategyPlugin registration seam for xlsx_s1_0027."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0027.config import XlsxS10027Config
from strategies.xlsx_s1_0027.strategy import XlsxS10027Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0027",
    config_cls=XlsxS10027Config,
    strategy_cls=XlsxS10027Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0027/config.yaml",
)
