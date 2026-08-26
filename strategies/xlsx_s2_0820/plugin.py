"""Normal StrategyPlugin registration seam for xlsx_s2_0820."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0820.config import XlsxS20820Config
from strategies.xlsx_s2_0820.strategy import XlsxS20820Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0820",
    config_cls=XlsxS20820Config,
    strategy_cls=XlsxS20820Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0820/config.yaml",
)
