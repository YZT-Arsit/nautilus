"""Normal StrategyPlugin registration seam for xlsx_s1_0461."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0461.config import XlsxS10461Config
from strategies.xlsx_s1_0461.strategy import XlsxS10461Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0461",
    config_cls=XlsxS10461Config,
    strategy_cls=XlsxS10461Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0461/config.yaml",
)
