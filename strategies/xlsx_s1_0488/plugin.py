"""Normal StrategyPlugin registration seam for xlsx_s1_0488."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0488.config import XlsxS10488Config
from strategies.xlsx_s1_0488.strategy import XlsxS10488Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0488",
    config_cls=XlsxS10488Config,
    strategy_cls=XlsxS10488Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0488/config.yaml",
)
