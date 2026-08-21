"""Normal StrategyPlugin registration seam for xlsx_s2_0601."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0601.config import XlsxS20601Config
from strategies.xlsx_s2_0601.strategy import XlsxS20601Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0601",
    config_cls=XlsxS20601Config,
    strategy_cls=XlsxS20601Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0601/config.yaml",
)
