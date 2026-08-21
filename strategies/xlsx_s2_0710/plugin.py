"""Normal StrategyPlugin registration seam for xlsx_s2_0710."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0710.config import XlsxS20710Config
from strategies.xlsx_s2_0710.strategy import XlsxS20710Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0710",
    config_cls=XlsxS20710Config,
    strategy_cls=XlsxS20710Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0710/config.yaml",
)
