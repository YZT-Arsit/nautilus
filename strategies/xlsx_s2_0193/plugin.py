"""Normal StrategyPlugin registration seam for xlsx_s2_0193."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0193.config import XlsxS20193Config
from strategies.xlsx_s2_0193.strategy import XlsxS20193Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0193",
    config_cls=XlsxS20193Config,
    strategy_cls=XlsxS20193Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0193/config.yaml",
)
