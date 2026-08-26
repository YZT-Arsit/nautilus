"""Normal StrategyPlugin registration seam for xlsx_s2_0705."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0705.config import XlsxS20705Config
from strategies.xlsx_s2_0705.strategy import XlsxS20705Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0705",
    config_cls=XlsxS20705Config,
    strategy_cls=XlsxS20705Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0705/config.yaml",
)
