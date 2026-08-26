"""Normal StrategyPlugin registration seam for xlsx_s2_0685."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0685.config import XlsxS20685Config
from strategies.xlsx_s2_0685.strategy import XlsxS20685Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0685",
    config_cls=XlsxS20685Config,
    strategy_cls=XlsxS20685Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0685/config.yaml",
)
