"""Normal StrategyPlugin registration seam for xlsx_s1_0479."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0479.config import XlsxS10479Config
from strategies.xlsx_s1_0479.strategy import XlsxS10479Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0479",
    config_cls=XlsxS10479Config,
    strategy_cls=XlsxS10479Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0479/config.yaml",
)
