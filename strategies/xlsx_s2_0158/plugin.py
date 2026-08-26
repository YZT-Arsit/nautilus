"""Normal StrategyPlugin registration seam for xlsx_s2_0158."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0158.config import XlsxS20158Config
from strategies.xlsx_s2_0158.strategy import XlsxS20158Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0158",
    config_cls=XlsxS20158Config,
    strategy_cls=XlsxS20158Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0158/config.yaml",
)
