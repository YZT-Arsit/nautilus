"""Normal StrategyPlugin registration seam for xlsx_s2_0230."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0230.config import XlsxS20230Config
from strategies.xlsx_s2_0230.strategy import XlsxS20230Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0230",
    config_cls=XlsxS20230Config,
    strategy_cls=XlsxS20230Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0230/config.yaml",
)
