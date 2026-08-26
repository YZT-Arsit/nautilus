"""Normal StrategyPlugin registration seam for xlsx_s2_0716."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0716.config import XlsxS20716Config
from strategies.xlsx_s2_0716.strategy import XlsxS20716Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0716",
    config_cls=XlsxS20716Config,
    strategy_cls=XlsxS20716Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0716/config.yaml",
)
