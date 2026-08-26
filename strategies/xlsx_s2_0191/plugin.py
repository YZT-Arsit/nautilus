"""Normal StrategyPlugin registration seam for xlsx_s2_0191."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0191.config import XlsxS20191Config
from strategies.xlsx_s2_0191.strategy import XlsxS20191Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0191",
    config_cls=XlsxS20191Config,
    strategy_cls=XlsxS20191Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0191/config.yaml",
)
