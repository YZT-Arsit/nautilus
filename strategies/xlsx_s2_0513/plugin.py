"""Normal StrategyPlugin registration seam for xlsx_s2_0513."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0513.config import XlsxS20513Config
from strategies.xlsx_s2_0513.strategy import XlsxS20513Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0513",
    config_cls=XlsxS20513Config,
    strategy_cls=XlsxS20513Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0513/config.yaml",
)
