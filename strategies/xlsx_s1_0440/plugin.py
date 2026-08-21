"""Normal StrategyPlugin registration seam for xlsx_s1_0440."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0440.config import XlsxS10440Config
from strategies.xlsx_s1_0440.strategy import XlsxS10440Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0440",
    config_cls=XlsxS10440Config,
    strategy_cls=XlsxS10440Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0440/config.yaml",
)
