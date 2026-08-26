"""Normal StrategyPlugin registration seam for xlsx_s2_0771."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0771.config import XlsxS20771Config
from strategies.xlsx_s2_0771.strategy import XlsxS20771Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0771",
    config_cls=XlsxS20771Config,
    strategy_cls=XlsxS20771Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0771/config.yaml",
)
