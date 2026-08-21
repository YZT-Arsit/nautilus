"""Normal StrategyPlugin registration seam for xlsx_s2_0536."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0536.config import XlsxS20536Config
from strategies.xlsx_s2_0536.strategy import XlsxS20536Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0536",
    config_cls=XlsxS20536Config,
    strategy_cls=XlsxS20536Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0536/config.yaml",
)
