"""Normal StrategyPlugin registration seam for xlsx_s2_0783."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0783.config import XlsxS20783Config
from strategies.xlsx_s2_0783.strategy import XlsxS20783Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0783",
    config_cls=XlsxS20783Config,
    strategy_cls=XlsxS20783Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0783/config.yaml",
)
