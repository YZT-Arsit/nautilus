"""Normal StrategyPlugin registration seam for xlsx_s2_0737."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0737.config import XlsxS20737Config
from strategies.xlsx_s2_0737.strategy import XlsxS20737Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0737",
    config_cls=XlsxS20737Config,
    strategy_cls=XlsxS20737Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0737/config.yaml",
)
