"""Normal StrategyPlugin registration seam for xlsx_s2_0821."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0821.config import XlsxS20821Config
from strategies.xlsx_s2_0821.strategy import XlsxS20821Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0821",
    config_cls=XlsxS20821Config,
    strategy_cls=XlsxS20821Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0821/config.yaml",
)
