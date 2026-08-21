"""Normal StrategyPlugin registration seam for xlsx_s1_0522."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0522.config import XlsxS10522Config
from strategies.xlsx_s1_0522.strategy import XlsxS10522Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0522",
    config_cls=XlsxS10522Config,
    strategy_cls=XlsxS10522Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0522/config.yaml",
)
