"""Normal StrategyPlugin registration seam for xlsx_s2_0257."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0257.config import XlsxS20257Config
from strategies.xlsx_s2_0257.strategy import XlsxS20257Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0257",
    config_cls=XlsxS20257Config,
    strategy_cls=XlsxS20257Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0257/config.yaml",
)
