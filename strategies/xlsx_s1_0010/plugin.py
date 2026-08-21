"""Normal StrategyPlugin registration seam for xlsx_s1_0010."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0010.config import XlsxS10010Config
from strategies.xlsx_s1_0010.strategy import XlsxS10010Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0010",
    config_cls=XlsxS10010Config,
    strategy_cls=XlsxS10010Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0010/config.yaml",
)
