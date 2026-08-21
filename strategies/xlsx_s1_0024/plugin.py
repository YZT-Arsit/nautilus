"""Normal StrategyPlugin registration seam for xlsx_s1_0024."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0024.config import XlsxS10024Config
from strategies.xlsx_s1_0024.strategy import XlsxS10024Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0024",
    config_cls=XlsxS10024Config,
    strategy_cls=XlsxS10024Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0024/config.yaml",
)
