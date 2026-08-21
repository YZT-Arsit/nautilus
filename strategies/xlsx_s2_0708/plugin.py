"""Normal StrategyPlugin registration seam for xlsx_s2_0708."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0708.config import XlsxS20708Config
from strategies.xlsx_s2_0708.strategy import XlsxS20708Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0708",
    config_cls=XlsxS20708Config,
    strategy_cls=XlsxS20708Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0708/config.yaml",
)
