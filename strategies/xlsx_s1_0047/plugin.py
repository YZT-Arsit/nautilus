"""Normal StrategyPlugin registration seam for xlsx_s1_0047."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0047.config import XlsxS10047Config
from strategies.xlsx_s1_0047.strategy import XlsxS10047Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0047",
    config_cls=XlsxS10047Config,
    strategy_cls=XlsxS10047Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0047/config.yaml",
)
