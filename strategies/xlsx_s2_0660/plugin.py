"""Normal StrategyPlugin registration seam for xlsx_s2_0660."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0660.config import XlsxS20660Config
from strategies.xlsx_s2_0660.strategy import XlsxS20660Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0660",
    config_cls=XlsxS20660Config,
    strategy_cls=XlsxS20660Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0660/config.yaml",
)
