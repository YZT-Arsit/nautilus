"""Normal StrategyPlugin registration seam for xlsx_s2_0337."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0337.config import XlsxS20337Config
from strategies.xlsx_s2_0337.strategy import XlsxS20337Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0337",
    config_cls=XlsxS20337Config,
    strategy_cls=XlsxS20337Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0337/config.yaml",
)
