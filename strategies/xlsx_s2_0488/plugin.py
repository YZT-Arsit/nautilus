"""Normal StrategyPlugin registration seam for xlsx_s2_0488."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0488.config import XlsxS20488Config
from strategies.xlsx_s2_0488.strategy import XlsxS20488Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0488",
    config_cls=XlsxS20488Config,
    strategy_cls=XlsxS20488Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0488/config.yaml",
)
