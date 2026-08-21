"""Normal StrategyPlugin registration seam for xlsx_s2_0713."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0713.config import XlsxS20713Config
from strategies.xlsx_s2_0713.strategy import XlsxS20713Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0713",
    config_cls=XlsxS20713Config,
    strategy_cls=XlsxS20713Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0713/config.yaml",
)
