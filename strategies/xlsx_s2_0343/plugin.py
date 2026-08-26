"""Normal StrategyPlugin registration seam for xlsx_s2_0343."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0343.config import XlsxS20343Config
from strategies.xlsx_s2_0343.strategy import XlsxS20343Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0343",
    config_cls=XlsxS20343Config,
    strategy_cls=XlsxS20343Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0343/config.yaml",
)
