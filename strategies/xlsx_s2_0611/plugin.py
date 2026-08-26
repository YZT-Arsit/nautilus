"""Normal StrategyPlugin registration seam for xlsx_s2_0611."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0611.config import XlsxS20611Config
from strategies.xlsx_s2_0611.strategy import XlsxS20611Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0611",
    config_cls=XlsxS20611Config,
    strategy_cls=XlsxS20611Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0611/config.yaml",
)
