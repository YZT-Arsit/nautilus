"""Normal StrategyPlugin registration seam for xlsx_s2_0258."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0258.config import XlsxS20258Config
from strategies.xlsx_s2_0258.strategy import XlsxS20258Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0258",
    config_cls=XlsxS20258Config,
    strategy_cls=XlsxS20258Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0258/config.yaml",
)
