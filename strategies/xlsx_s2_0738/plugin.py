"""Normal StrategyPlugin registration seam for xlsx_s2_0738."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0738.config import XlsxS20738Config
from strategies.xlsx_s2_0738.strategy import XlsxS20738Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0738",
    config_cls=XlsxS20738Config,
    strategy_cls=XlsxS20738Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0738/config.yaml",
)
