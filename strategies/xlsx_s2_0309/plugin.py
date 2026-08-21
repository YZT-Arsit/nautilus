"""Normal StrategyPlugin registration seam for xlsx_s2_0309."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0309.config import XlsxS20309Config
from strategies.xlsx_s2_0309.strategy import XlsxS20309Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0309",
    config_cls=XlsxS20309Config,
    strategy_cls=XlsxS20309Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0309/config.yaml",
)
