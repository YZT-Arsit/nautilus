"""Normal StrategyPlugin registration seam for xlsx_s2_0845."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0845.config import XlsxS20845Config
from strategies.xlsx_s2_0845.strategy import XlsxS20845Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0845",
    config_cls=XlsxS20845Config,
    strategy_cls=XlsxS20845Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0845/config.yaml",
)
