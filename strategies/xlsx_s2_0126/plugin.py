"""Normal StrategyPlugin registration seam for xlsx_s2_0126."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0126.config import XlsxS20126Config
from strategies.xlsx_s2_0126.strategy import XlsxS20126Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0126",
    config_cls=XlsxS20126Config,
    strategy_cls=XlsxS20126Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0126/config.yaml",
)
