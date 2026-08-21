"""Normal StrategyPlugin registration seam for xlsx_s2_0884."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0884.config import XlsxS20884Config
from strategies.xlsx_s2_0884.strategy import XlsxS20884Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0884",
    config_cls=XlsxS20884Config,
    strategy_cls=XlsxS20884Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0884/config.yaml",
)
