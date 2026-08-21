"""Normal StrategyPlugin registration seam for xlsx_s1_0033."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0033.config import XlsxS10033Config
from strategies.xlsx_s1_0033.strategy import XlsxS10033Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0033",
    config_cls=XlsxS10033Config,
    strategy_cls=XlsxS10033Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0033/config.yaml",
)
