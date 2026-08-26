"""Normal StrategyPlugin registration seam for xlsx_s2_0203."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0203.config import XlsxS20203Config
from strategies.xlsx_s2_0203.strategy import XlsxS20203Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0203",
    config_cls=XlsxS20203Config,
    strategy_cls=XlsxS20203Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0203/config.yaml",
)
