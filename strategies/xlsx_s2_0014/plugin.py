"""Normal StrategyPlugin registration seam for xlsx_s2_0014."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0014.config import XlsxS20014Config
from strategies.xlsx_s2_0014.strategy import XlsxS20014Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0014",
    config_cls=XlsxS20014Config,
    strategy_cls=XlsxS20014Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0014/config.yaml",
)
