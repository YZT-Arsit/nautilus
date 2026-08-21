"""Normal StrategyPlugin registration seam for xlsx_s2_0541."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0541.config import XlsxS20541Config
from strategies.xlsx_s2_0541.strategy import XlsxS20541Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0541",
    config_cls=XlsxS20541Config,
    strategy_cls=XlsxS20541Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0541/config.yaml",
)
