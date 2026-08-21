"""Normal StrategyPlugin registration seam for xlsx_s2_0535."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0535.config import XlsxS20535Config
from strategies.xlsx_s2_0535.strategy import XlsxS20535Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0535",
    config_cls=XlsxS20535Config,
    strategy_cls=XlsxS20535Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0535/config.yaml",
)
