"""Normal StrategyPlugin registration seam for xlsx_s2_0824."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0824.config import XlsxS20824Config
from strategies.xlsx_s2_0824.strategy import XlsxS20824Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0824",
    config_cls=XlsxS20824Config,
    strategy_cls=XlsxS20824Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0824/config.yaml",
)
