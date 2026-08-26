"""Normal StrategyPlugin registration seam for xlsx_s2_0606."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0606.config import XlsxS20606Config
from strategies.xlsx_s2_0606.strategy import XlsxS20606Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0606",
    config_cls=XlsxS20606Config,
    strategy_cls=XlsxS20606Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0606/config.yaml",
)
