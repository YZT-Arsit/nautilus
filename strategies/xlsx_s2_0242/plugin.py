"""Normal StrategyPlugin registration seam for xlsx_s2_0242."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0242.config import XlsxS20242Config
from strategies.xlsx_s2_0242.strategy import XlsxS20242Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0242",
    config_cls=XlsxS20242Config,
    strategy_cls=XlsxS20242Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0242/config.yaml",
)
