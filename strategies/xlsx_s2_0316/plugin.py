"""Normal StrategyPlugin registration seam for xlsx_s2_0316."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0316.config import XlsxS20316Config
from strategies.xlsx_s2_0316.strategy import XlsxS20316Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0316",
    config_cls=XlsxS20316Config,
    strategy_cls=XlsxS20316Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0316/config.yaml",
)
