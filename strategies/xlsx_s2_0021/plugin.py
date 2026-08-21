"""Normal StrategyPlugin registration seam for xlsx_s2_0021."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0021.config import XlsxS20021Config
from strategies.xlsx_s2_0021.strategy import XlsxS20021Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0021",
    config_cls=XlsxS20021Config,
    strategy_cls=XlsxS20021Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0021/config.yaml",
)
