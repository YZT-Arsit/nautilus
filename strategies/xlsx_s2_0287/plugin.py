"""Normal StrategyPlugin registration seam for xlsx_s2_0287."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0287.config import XlsxS20287Config
from strategies.xlsx_s2_0287.strategy import XlsxS20287Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0287",
    config_cls=XlsxS20287Config,
    strategy_cls=XlsxS20287Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0287/config.yaml",
)
