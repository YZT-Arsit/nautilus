"""Normal StrategyPlugin registration seam for xlsx_s2_0347."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0347.config import XlsxS20347Config
from strategies.xlsx_s2_0347.strategy import XlsxS20347Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0347",
    config_cls=XlsxS20347Config,
    strategy_cls=XlsxS20347Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0347/config.yaml",
)
