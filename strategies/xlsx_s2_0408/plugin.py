"""Normal StrategyPlugin registration seam for xlsx_s2_0408."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0408.config import XlsxS20408Config
from strategies.xlsx_s2_0408.strategy import XlsxS20408Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0408",
    config_cls=XlsxS20408Config,
    strategy_cls=XlsxS20408Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0408/config.yaml",
)
