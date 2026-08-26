"""Normal StrategyPlugin registration seam for xlsx_s2_0124."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0124.config import XlsxS20124Config
from strategies.xlsx_s2_0124.strategy import XlsxS20124Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0124",
    config_cls=XlsxS20124Config,
    strategy_cls=XlsxS20124Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0124/config.yaml",
)
