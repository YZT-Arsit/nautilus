"""Normal StrategyPlugin registration seam for xlsx_s2_0404."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0404.config import XlsxS20404Config
from strategies.xlsx_s2_0404.strategy import XlsxS20404Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0404",
    config_cls=XlsxS20404Config,
    strategy_cls=XlsxS20404Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0404/config.yaml",
)
