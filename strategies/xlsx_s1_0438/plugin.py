"""Normal StrategyPlugin registration seam for xlsx_s1_0438."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0438.config import XlsxS10438Config
from strategies.xlsx_s1_0438.strategy import XlsxS10438Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0438",
    config_cls=XlsxS10438Config,
    strategy_cls=XlsxS10438Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0438/config.yaml",
)
