"""Normal StrategyPlugin registration seam for xlsx_s2_0396."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0396.config import XlsxS20396Config
from strategies.xlsx_s2_0396.strategy import XlsxS20396Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0396",
    config_cls=XlsxS20396Config,
    strategy_cls=XlsxS20396Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0396/config.yaml",
)
