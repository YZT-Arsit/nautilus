"""Normal StrategyPlugin registration seam for xlsx_s2_0240."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0240.config import XlsxS20240Config
from strategies.xlsx_s2_0240.strategy import XlsxS20240Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0240",
    config_cls=XlsxS20240Config,
    strategy_cls=XlsxS20240Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0240/config.yaml",
)
