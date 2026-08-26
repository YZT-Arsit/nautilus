"""Normal StrategyPlugin registration seam for xlsx_s2_0693."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0693.config import XlsxS20693Config
from strategies.xlsx_s2_0693.strategy import XlsxS20693Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0693",
    config_cls=XlsxS20693Config,
    strategy_cls=XlsxS20693Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0693/config.yaml",
)
