"""Normal StrategyPlugin registration seam for xlsx_s2_0065."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0065.config import XlsxS20065Config
from strategies.xlsx_s2_0065.strategy import XlsxS20065Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0065",
    config_cls=XlsxS20065Config,
    strategy_cls=XlsxS20065Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0065/config.yaml",
)
