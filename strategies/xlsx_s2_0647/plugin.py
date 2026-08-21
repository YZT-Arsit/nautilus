"""Normal StrategyPlugin registration seam for xlsx_s2_0647."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0647.config import XlsxS20647Config
from strategies.xlsx_s2_0647.strategy import XlsxS20647Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0647",
    config_cls=XlsxS20647Config,
    strategy_cls=XlsxS20647Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0647/config.yaml",
)
