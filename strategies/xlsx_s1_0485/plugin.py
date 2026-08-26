"""Normal StrategyPlugin registration seam for xlsx_s1_0485."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0485.config import XlsxS10485Config
from strategies.xlsx_s1_0485.strategy import XlsxS10485Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0485",
    config_cls=XlsxS10485Config,
    strategy_cls=XlsxS10485Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0485/config.yaml",
)
