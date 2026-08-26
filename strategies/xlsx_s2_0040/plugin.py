"""Normal StrategyPlugin registration seam for xlsx_s2_0040."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0040.config import XlsxS20040Config
from strategies.xlsx_s2_0040.strategy import XlsxS20040Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0040",
    config_cls=XlsxS20040Config,
    strategy_cls=XlsxS20040Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0040/config.yaml",
)
