"""Normal StrategyPlugin registration seam for xlsx_s2_0644."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0644.config import XlsxS20644Config
from strategies.xlsx_s2_0644.strategy import XlsxS20644Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0644",
    config_cls=XlsxS20644Config,
    strategy_cls=XlsxS20644Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0644/config.yaml",
)
