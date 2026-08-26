"""Normal StrategyPlugin registration seam for xlsx_s2_0342."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0342.config import XlsxS20342Config
from strategies.xlsx_s2_0342.strategy import XlsxS20342Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0342",
    config_cls=XlsxS20342Config,
    strategy_cls=XlsxS20342Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0342/config.yaml",
)
