"""Normal StrategyPlugin registration seam for xlsx_s2_0778."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0778.config import XlsxS20778Config
from strategies.xlsx_s2_0778.strategy import XlsxS20778Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0778",
    config_cls=XlsxS20778Config,
    strategy_cls=XlsxS20778Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0778/config.yaml",
)
