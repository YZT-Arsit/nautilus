"""Normal StrategyPlugin registration seam for xlsx_s2_0780."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0780.config import XlsxS20780Config
from strategies.xlsx_s2_0780.strategy import XlsxS20780Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0780",
    config_cls=XlsxS20780Config,
    strategy_cls=XlsxS20780Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0780/config.yaml",
)
