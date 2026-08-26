"""Normal StrategyPlugin registration seam for xlsx_s2_0414."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0414.config import XlsxS20414Config
from strategies.xlsx_s2_0414.strategy import XlsxS20414Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0414",
    config_cls=XlsxS20414Config,
    strategy_cls=XlsxS20414Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0414/config.yaml",
)
