"""Normal StrategyPlugin registration seam for xlsx_s2_0663."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0663.config import XlsxS20663Config
from strategies.xlsx_s2_0663.strategy import XlsxS20663Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0663",
    config_cls=XlsxS20663Config,
    strategy_cls=XlsxS20663Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0663/config.yaml",
)
