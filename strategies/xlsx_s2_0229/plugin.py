"""Normal StrategyPlugin registration seam for xlsx_s2_0229."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0229.config import XlsxS20229Config
from strategies.xlsx_s2_0229.strategy import XlsxS20229Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0229",
    config_cls=XlsxS20229Config,
    strategy_cls=XlsxS20229Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0229/config.yaml",
)
