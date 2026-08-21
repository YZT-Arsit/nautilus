"""Normal StrategyPlugin registration seam for xlsx_s1_0034."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0034.config import XlsxS10034Config
from strategies.xlsx_s1_0034.strategy import XlsxS10034Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0034",
    config_cls=XlsxS10034Config,
    strategy_cls=XlsxS10034Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0034/config.yaml",
)
