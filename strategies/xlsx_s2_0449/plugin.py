"""Normal StrategyPlugin registration seam for xlsx_s2_0449."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0449.config import XlsxS20449Config
from strategies.xlsx_s2_0449.strategy import XlsxS20449Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0449",
    config_cls=XlsxS20449Config,
    strategy_cls=XlsxS20449Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0449/config.yaml",
)
