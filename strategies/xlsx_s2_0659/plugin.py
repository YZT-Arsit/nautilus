"""Normal StrategyPlugin registration seam for xlsx_s2_0659."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0659.config import XlsxS20659Config
from strategies.xlsx_s2_0659.strategy import XlsxS20659Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0659",
    config_cls=XlsxS20659Config,
    strategy_cls=XlsxS20659Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0659/config.yaml",
)
