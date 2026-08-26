"""Normal StrategyPlugin registration seam for xlsx_s2_0882."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0882.config import XlsxS20882Config
from strategies.xlsx_s2_0882.strategy import XlsxS20882Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0882",
    config_cls=XlsxS20882Config,
    strategy_cls=XlsxS20882Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0882/config.yaml",
)
