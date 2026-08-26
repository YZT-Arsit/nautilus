"""Normal StrategyPlugin registration seam for xlsx_s2_0725."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0725.config import XlsxS20725Config
from strategies.xlsx_s2_0725.strategy import XlsxS20725Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0725",
    config_cls=XlsxS20725Config,
    strategy_cls=XlsxS20725Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0725/config.yaml",
)
