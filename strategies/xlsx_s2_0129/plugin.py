"""Normal StrategyPlugin registration seam for xlsx_s2_0129."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0129.config import XlsxS20129Config
from strategies.xlsx_s2_0129.strategy import XlsxS20129Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0129",
    config_cls=XlsxS20129Config,
    strategy_cls=XlsxS20129Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0129/config.yaml",
)
