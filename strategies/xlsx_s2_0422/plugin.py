"""Normal StrategyPlugin registration seam for xlsx_s2_0422."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0422.config import XlsxS20422Config
from strategies.xlsx_s2_0422.strategy import XlsxS20422Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0422",
    config_cls=XlsxS20422Config,
    strategy_cls=XlsxS20422Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0422/config.yaml",
)
