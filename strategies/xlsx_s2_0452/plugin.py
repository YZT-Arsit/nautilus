"""Normal StrategyPlugin registration seam for xlsx_s2_0452."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0452.config import XlsxS20452Config
from strategies.xlsx_s2_0452.strategy import XlsxS20452Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0452",
    config_cls=XlsxS20452Config,
    strategy_cls=XlsxS20452Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0452/config.yaml",
)
