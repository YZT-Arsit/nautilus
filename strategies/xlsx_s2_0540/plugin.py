"""Normal StrategyPlugin registration seam for xlsx_s2_0540."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0540.config import XlsxS20540Config
from strategies.xlsx_s2_0540.strategy import XlsxS20540Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0540",
    config_cls=XlsxS20540Config,
    strategy_cls=XlsxS20540Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0540/config.yaml",
)
