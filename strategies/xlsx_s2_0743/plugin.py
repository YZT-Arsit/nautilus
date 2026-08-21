"""Normal StrategyPlugin registration seam for xlsx_s2_0743."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0743.config import XlsxS20743Config
from strategies.xlsx_s2_0743.strategy import XlsxS20743Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0743",
    config_cls=XlsxS20743Config,
    strategy_cls=XlsxS20743Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0743/config.yaml",
)
