"""Normal StrategyPlugin registration seam for xlsx_s2_0718."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0718.config import XlsxS20718Config
from strategies.xlsx_s2_0718.strategy import XlsxS20718Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0718",
    config_cls=XlsxS20718Config,
    strategy_cls=XlsxS20718Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0718/config.yaml",
)
