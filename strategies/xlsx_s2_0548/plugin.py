"""Normal StrategyPlugin registration seam for xlsx_s2_0548."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0548.config import XlsxS20548Config
from strategies.xlsx_s2_0548.strategy import XlsxS20548Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0548",
    config_cls=XlsxS20548Config,
    strategy_cls=XlsxS20548Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0548/config.yaml",
)
