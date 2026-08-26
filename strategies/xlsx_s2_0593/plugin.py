"""Normal StrategyPlugin registration seam for xlsx_s2_0593."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0593.config import XlsxS20593Config
from strategies.xlsx_s2_0593.strategy import XlsxS20593Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0593",
    config_cls=XlsxS20593Config,
    strategy_cls=XlsxS20593Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0593/config.yaml",
)
