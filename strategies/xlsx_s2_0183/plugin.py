"""Normal StrategyPlugin registration seam for xlsx_s2_0183."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0183.config import XlsxS20183Config
from strategies.xlsx_s2_0183.strategy import XlsxS20183Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0183",
    config_cls=XlsxS20183Config,
    strategy_cls=XlsxS20183Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0183/config.yaml",
)
