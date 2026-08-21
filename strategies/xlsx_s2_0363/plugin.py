"""Normal StrategyPlugin registration seam for xlsx_s2_0363."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0363.config import XlsxS20363Config
from strategies.xlsx_s2_0363.strategy import XlsxS20363Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0363",
    config_cls=XlsxS20363Config,
    strategy_cls=XlsxS20363Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0363/config.yaml",
)
