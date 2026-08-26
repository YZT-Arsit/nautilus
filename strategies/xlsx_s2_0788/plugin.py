"""Normal StrategyPlugin registration seam for xlsx_s2_0788."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0788.config import XlsxS20788Config
from strategies.xlsx_s2_0788.strategy import XlsxS20788Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0788",
    config_cls=XlsxS20788Config,
    strategy_cls=XlsxS20788Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0788/config.yaml",
)
