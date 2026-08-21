"""Normal StrategyPlugin registration seam for xlsx_s2_0059."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0059.config import XlsxS20059Config
from strategies.xlsx_s2_0059.strategy import XlsxS20059Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0059",
    config_cls=XlsxS20059Config,
    strategy_cls=XlsxS20059Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0059/config.yaml",
)
