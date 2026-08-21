"""Normal StrategyPlugin registration seam for xlsx_s2_0192."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0192.config import XlsxS20192Config
from strategies.xlsx_s2_0192.strategy import XlsxS20192Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0192",
    config_cls=XlsxS20192Config,
    strategy_cls=XlsxS20192Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0192/config.yaml",
)
