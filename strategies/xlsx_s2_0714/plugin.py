"""Normal StrategyPlugin registration seam for xlsx_s2_0714."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0714.config import XlsxS20714Config
from strategies.xlsx_s2_0714.strategy import XlsxS20714Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0714",
    config_cls=XlsxS20714Config,
    strategy_cls=XlsxS20714Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0714/config.yaml",
)
