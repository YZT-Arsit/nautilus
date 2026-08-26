"""Normal StrategyPlugin registration seam for xlsx_s2_0770."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0770.config import XlsxS20770Config
from strategies.xlsx_s2_0770.strategy import XlsxS20770Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0770",
    config_cls=XlsxS20770Config,
    strategy_cls=XlsxS20770Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0770/config.yaml",
)
