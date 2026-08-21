"""Normal StrategyPlugin registration seam for xlsx_s1_0432."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0432.config import XlsxS10432Config
from strategies.xlsx_s1_0432.strategy import XlsxS10432Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0432",
    config_cls=XlsxS10432Config,
    strategy_cls=XlsxS10432Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0432/config.yaml",
)
