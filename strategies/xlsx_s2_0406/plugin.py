"""Normal StrategyPlugin registration seam for xlsx_s2_0406."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0406.config import XlsxS20406Config
from strategies.xlsx_s2_0406.strategy import XlsxS20406Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0406",
    config_cls=XlsxS20406Config,
    strategy_cls=XlsxS20406Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0406/config.yaml",
)
