"""Normal StrategyPlugin registration seam for xlsx_s2_0122."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0122.config import XlsxS20122Config
from strategies.xlsx_s2_0122.strategy import XlsxS20122Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0122",
    config_cls=XlsxS20122Config,
    strategy_cls=XlsxS20122Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0122/config.yaml",
)
