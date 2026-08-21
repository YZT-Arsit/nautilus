"""Normal StrategyPlugin registration seam for xlsx_s2_0632."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0632.config import XlsxS20632Config
from strategies.xlsx_s2_0632.strategy import XlsxS20632Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0632",
    config_cls=XlsxS20632Config,
    strategy_cls=XlsxS20632Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0632/config.yaml",
)
