"""Normal StrategyPlugin registration seam for xlsx_s2_0485."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0485.config import XlsxS20485Config
from strategies.xlsx_s2_0485.strategy import XlsxS20485Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0485",
    config_cls=XlsxS20485Config,
    strategy_cls=XlsxS20485Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0485/config.yaml",
)
