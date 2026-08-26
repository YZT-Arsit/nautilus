"""Normal StrategyPlugin registration seam for xlsx_s2_0525."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0525.config import XlsxS20525Config
from strategies.xlsx_s2_0525.strategy import XlsxS20525Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0525",
    config_cls=XlsxS20525Config,
    strategy_cls=XlsxS20525Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0525/config.yaml",
)
