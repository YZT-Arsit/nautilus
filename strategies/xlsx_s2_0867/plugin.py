"""Normal StrategyPlugin registration seam for xlsx_s2_0867."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0867.config import XlsxS20867Config
from strategies.xlsx_s2_0867.strategy import XlsxS20867Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0867",
    config_cls=XlsxS20867Config,
    strategy_cls=XlsxS20867Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0867/config.yaml",
)
