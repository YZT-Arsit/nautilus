"""Normal StrategyPlugin registration seam for xlsx_s1_0019."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0019.config import XlsxS10019Config
from strategies.xlsx_s1_0019.strategy import XlsxS10019Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0019",
    config_cls=XlsxS10019Config,
    strategy_cls=XlsxS10019Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0019/config.yaml",
)
