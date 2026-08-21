"""Normal StrategyPlugin registration seam for xlsx_s1_0006."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0006.config import XlsxS10006Config
from strategies.xlsx_s1_0006.strategy import XlsxS10006Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0006",
    config_cls=XlsxS10006Config,
    strategy_cls=XlsxS10006Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0006/config.yaml",
)
