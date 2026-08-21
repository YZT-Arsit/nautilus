"""Normal StrategyPlugin registration seam for xlsx_s2_0432."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0432.config import XlsxS20432Config
from strategies.xlsx_s2_0432.strategy import XlsxS20432Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0432",
    config_cls=XlsxS20432Config,
    strategy_cls=XlsxS20432Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0432/config.yaml",
)
