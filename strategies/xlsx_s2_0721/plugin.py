"""Normal StrategyPlugin registration seam for xlsx_s2_0721."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0721.config import XlsxS20721Config
from strategies.xlsx_s2_0721.strategy import XlsxS20721Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0721",
    config_cls=XlsxS20721Config,
    strategy_cls=XlsxS20721Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0721/config.yaml",
)
