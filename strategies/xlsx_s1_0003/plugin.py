"""Normal StrategyPlugin registration seam for xlsx_s1_0003."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0003.config import XlsxS10003Config
from strategies.xlsx_s1_0003.strategy import XlsxS10003Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0003",
    config_cls=XlsxS10003Config,
    strategy_cls=XlsxS10003Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0003/config.yaml",
)
