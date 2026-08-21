"""Normal StrategyPlugin registration seam for xlsx_s1_0467."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0467.config import XlsxS10467Config
from strategies.xlsx_s1_0467.strategy import XlsxS10467Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0467",
    config_cls=XlsxS10467Config,
    strategy_cls=XlsxS10467Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0467/config.yaml",
)
