"""Normal StrategyPlugin registration seam for xlsx_s2_0812."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0812.config import XlsxS20812Config
from strategies.xlsx_s2_0812.strategy import XlsxS20812Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0812",
    config_cls=XlsxS20812Config,
    strategy_cls=XlsxS20812Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0812/config.yaml",
)
