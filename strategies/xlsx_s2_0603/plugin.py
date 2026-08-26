"""Normal StrategyPlugin registration seam for xlsx_s2_0603."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0603.config import XlsxS20603Config
from strategies.xlsx_s2_0603.strategy import XlsxS20603Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0603",
    config_cls=XlsxS20603Config,
    strategy_cls=XlsxS20603Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0603/config.yaml",
)
