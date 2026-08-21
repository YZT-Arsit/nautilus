"""Normal StrategyPlugin registration seam for xlsx_s1_0040."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0040.config import XlsxS10040Config
from strategies.xlsx_s1_0040.strategy import XlsxS10040Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0040",
    config_cls=XlsxS10040Config,
    strategy_cls=XlsxS10040Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0040/config.yaml",
)
