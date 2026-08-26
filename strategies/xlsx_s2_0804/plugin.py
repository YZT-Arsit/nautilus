"""Normal StrategyPlugin registration seam for xlsx_s2_0804."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0804.config import XlsxS20804Config
from strategies.xlsx_s2_0804.strategy import XlsxS20804Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0804",
    config_cls=XlsxS20804Config,
    strategy_cls=XlsxS20804Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0804/config.yaml",
)
