"""Normal StrategyPlugin registration seam for xlsx_s1_0007."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0007.config import XlsxS10007Config
from strategies.xlsx_s1_0007.strategy import XlsxS10007Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0007",
    config_cls=XlsxS10007Config,
    strategy_cls=XlsxS10007Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0007/config.yaml",
)
