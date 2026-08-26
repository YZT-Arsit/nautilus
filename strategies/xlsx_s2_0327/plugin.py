"""Normal StrategyPlugin registration seam for xlsx_s2_0327."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0327.config import XlsxS20327Config
from strategies.xlsx_s2_0327.strategy import XlsxS20327Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0327",
    config_cls=XlsxS20327Config,
    strategy_cls=XlsxS20327Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0327/config.yaml",
)
