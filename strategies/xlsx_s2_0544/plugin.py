"""Normal StrategyPlugin registration seam for xlsx_s2_0544."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0544.config import XlsxS20544Config
from strategies.xlsx_s2_0544.strategy import XlsxS20544Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0544",
    config_cls=XlsxS20544Config,
    strategy_cls=XlsxS20544Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0544/config.yaml",
)
