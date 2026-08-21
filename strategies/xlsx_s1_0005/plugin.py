"""Normal StrategyPlugin registration seam for xlsx_s1_0005."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0005.config import XlsxS10005Config
from strategies.xlsx_s1_0005.strategy import XlsxS10005Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0005",
    config_cls=XlsxS10005Config,
    strategy_cls=XlsxS10005Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0005/config.yaml",
)
