"""Normal StrategyPlugin registration seam for xlsx_s2_0668."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0668.config import XlsxS20668Config
from strategies.xlsx_s2_0668.strategy import XlsxS20668Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0668",
    config_cls=XlsxS20668Config,
    strategy_cls=XlsxS20668Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0668/config.yaml",
)
