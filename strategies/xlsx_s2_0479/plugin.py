"""Normal StrategyPlugin registration seam for xlsx_s2_0479."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0479.config import XlsxS20479Config
from strategies.xlsx_s2_0479.strategy import XlsxS20479Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0479",
    config_cls=XlsxS20479Config,
    strategy_cls=XlsxS20479Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0479/config.yaml",
)
