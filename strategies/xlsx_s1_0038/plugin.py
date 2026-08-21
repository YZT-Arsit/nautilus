"""Normal StrategyPlugin registration seam for xlsx_s1_0038."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0038.config import XlsxS10038Config
from strategies.xlsx_s1_0038.strategy import XlsxS10038Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0038",
    config_cls=XlsxS10038Config,
    strategy_cls=XlsxS10038Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0038/config.yaml",
)
