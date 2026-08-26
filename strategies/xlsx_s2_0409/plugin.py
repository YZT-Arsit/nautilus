"""Normal StrategyPlugin registration seam for xlsx_s2_0409."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0409.config import XlsxS20409Config
from strategies.xlsx_s2_0409.strategy import XlsxS20409Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0409",
    config_cls=XlsxS20409Config,
    strategy_cls=XlsxS20409Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0409/config.yaml",
)
