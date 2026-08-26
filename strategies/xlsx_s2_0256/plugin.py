"""Normal StrategyPlugin registration seam for xlsx_s2_0256."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0256.config import XlsxS20256Config
from strategies.xlsx_s2_0256.strategy import XlsxS20256Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0256",
    config_cls=XlsxS20256Config,
    strategy_cls=XlsxS20256Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0256/config.yaml",
)
