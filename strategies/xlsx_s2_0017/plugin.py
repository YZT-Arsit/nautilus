"""Normal StrategyPlugin registration seam for xlsx_s2_0017."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0017.config import XlsxS20017Config
from strategies.xlsx_s2_0017.strategy import XlsxS20017Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0017",
    config_cls=XlsxS20017Config,
    strategy_cls=XlsxS20017Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0017/config.yaml",
)
