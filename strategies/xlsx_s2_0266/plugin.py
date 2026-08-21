"""Normal StrategyPlugin registration seam for xlsx_s2_0266."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0266.config import XlsxS20266Config
from strategies.xlsx_s2_0266.strategy import XlsxS20266Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0266",
    config_cls=XlsxS20266Config,
    strategy_cls=XlsxS20266Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0266/config.yaml",
)
