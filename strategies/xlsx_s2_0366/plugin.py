"""Normal StrategyPlugin registration seam for xlsx_s2_0366."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0366.config import XlsxS20366Config
from strategies.xlsx_s2_0366.strategy import XlsxS20366Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0366",
    config_cls=XlsxS20366Config,
    strategy_cls=XlsxS20366Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0366/config.yaml",
)
