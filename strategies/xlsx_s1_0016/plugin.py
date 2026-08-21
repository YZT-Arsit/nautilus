"""Normal StrategyPlugin registration seam for xlsx_s1_0016."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0016.config import XlsxS10016Config
from strategies.xlsx_s1_0016.strategy import XlsxS10016Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0016",
    config_cls=XlsxS10016Config,
    strategy_cls=XlsxS10016Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0016/config.yaml",
)
