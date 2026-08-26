"""Normal StrategyPlugin registration seam for xlsx_s2_0571."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0571.config import XlsxS20571Config
from strategies.xlsx_s2_0571.strategy import XlsxS20571Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0571",
    config_cls=XlsxS20571Config,
    strategy_cls=XlsxS20571Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0571/config.yaml",
)
