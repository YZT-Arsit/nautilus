"""Normal StrategyPlugin registration seam for xlsx_s2_0627."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0627.config import XlsxS20627Config
from strategies.xlsx_s2_0627.strategy import XlsxS20627Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0627",
    config_cls=XlsxS20627Config,
    strategy_cls=XlsxS20627Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0627/config.yaml",
)
