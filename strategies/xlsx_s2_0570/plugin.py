"""Normal StrategyPlugin registration seam for xlsx_s2_0570."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0570.config import XlsxS20570Config
from strategies.xlsx_s2_0570.strategy import XlsxS20570Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0570",
    config_cls=XlsxS20570Config,
    strategy_cls=XlsxS20570Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0570/config.yaml",
)
