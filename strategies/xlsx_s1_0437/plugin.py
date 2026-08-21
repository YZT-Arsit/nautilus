"""Normal StrategyPlugin registration seam for xlsx_s1_0437."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0437.config import XlsxS10437Config
from strategies.xlsx_s1_0437.strategy import XlsxS10437Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0437",
    config_cls=XlsxS10437Config,
    strategy_cls=XlsxS10437Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0437/config.yaml",
)
