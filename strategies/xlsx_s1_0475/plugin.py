"""Normal StrategyPlugin registration seam for xlsx_s1_0475."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0475.config import XlsxS10475Config
from strategies.xlsx_s1_0475.strategy import XlsxS10475Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0475",
    config_cls=XlsxS10475Config,
    strategy_cls=XlsxS10475Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0475/config.yaml",
)
