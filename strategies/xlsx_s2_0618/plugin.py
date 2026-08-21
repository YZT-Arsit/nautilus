"""Normal StrategyPlugin registration seam for xlsx_s2_0618."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0618.config import XlsxS20618Config
from strategies.xlsx_s2_0618.strategy import XlsxS20618Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0618",
    config_cls=XlsxS20618Config,
    strategy_cls=XlsxS20618Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0618/config.yaml",
)
