"""Normal StrategyPlugin registration seam for xlsx_s2_0769."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0769.config import XlsxS20769Config
from strategies.xlsx_s2_0769.strategy import XlsxS20769Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0769",
    config_cls=XlsxS20769Config,
    strategy_cls=XlsxS20769Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0769/config.yaml",
)
