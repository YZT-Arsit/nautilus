"""Normal StrategyPlugin registration seam for xlsx_s2_0328."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0328.config import XlsxS20328Config
from strategies.xlsx_s2_0328.strategy import XlsxS20328Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0328",
    config_cls=XlsxS20328Config,
    strategy_cls=XlsxS20328Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0328/config.yaml",
)
