"""Normal StrategyPlugin registration seam for xlsx_s1_0508."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0508.config import XlsxS10508Config
from strategies.xlsx_s1_0508.strategy import XlsxS10508Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0508",
    config_cls=XlsxS10508Config,
    strategy_cls=XlsxS10508Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0508/config.yaml",
)
