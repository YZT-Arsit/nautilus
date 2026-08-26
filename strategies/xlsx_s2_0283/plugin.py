"""Normal StrategyPlugin registration seam for xlsx_s2_0283."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0283.config import XlsxS20283Config
from strategies.xlsx_s2_0283.strategy import XlsxS20283Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0283",
    config_cls=XlsxS20283Config,
    strategy_cls=XlsxS20283Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0283/config.yaml",
)
