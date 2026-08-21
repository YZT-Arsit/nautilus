"""Normal StrategyPlugin registration seam for xlsx_s1_0503."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0503.config import XlsxS10503Config
from strategies.xlsx_s1_0503.strategy import XlsxS10503Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0503",
    config_cls=XlsxS10503Config,
    strategy_cls=XlsxS10503Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0503/config.yaml",
)
