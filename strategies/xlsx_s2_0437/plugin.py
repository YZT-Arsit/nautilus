"""Normal StrategyPlugin registration seam for xlsx_s2_0437."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0437.config import XlsxS20437Config
from strategies.xlsx_s2_0437.strategy import XlsxS20437Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0437",
    config_cls=XlsxS20437Config,
    strategy_cls=XlsxS20437Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0437/config.yaml",
)
