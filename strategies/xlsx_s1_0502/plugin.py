"""Normal StrategyPlugin registration seam for xlsx_s1_0502."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0502.config import XlsxS10502Config
from strategies.xlsx_s1_0502.strategy import XlsxS10502Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0502",
    config_cls=XlsxS10502Config,
    strategy_cls=XlsxS10502Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0502/config.yaml",
)
