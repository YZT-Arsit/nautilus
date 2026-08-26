"""Normal StrategyPlugin registration seam for xlsx_s1_0494."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0494.config import XlsxS10494Config
from strategies.xlsx_s1_0494.strategy import XlsxS10494Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0494",
    config_cls=XlsxS10494Config,
    strategy_cls=XlsxS10494Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0494/config.yaml",
)
