"""Normal StrategyPlugin registration seam for xlsx_s1_0017."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0017.config import XlsxS10017Config
from strategies.xlsx_s1_0017.strategy import XlsxS10017Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0017",
    config_cls=XlsxS10017Config,
    strategy_cls=XlsxS10017Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0017/config.yaml",
)
