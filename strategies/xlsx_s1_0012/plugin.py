"""Normal StrategyPlugin registration seam for xlsx_s1_0012."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0012.config import XlsxS10012Config
from strategies.xlsx_s1_0012.strategy import XlsxS10012Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0012",
    config_cls=XlsxS10012Config,
    strategy_cls=XlsxS10012Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0012/config.yaml",
)
