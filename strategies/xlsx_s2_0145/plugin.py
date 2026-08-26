"""Normal StrategyPlugin registration seam for xlsx_s2_0145."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0145.config import XlsxS20145Config
from strategies.xlsx_s2_0145.strategy import XlsxS20145Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0145",
    config_cls=XlsxS20145Config,
    strategy_cls=XlsxS20145Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0145/config.yaml",
)
