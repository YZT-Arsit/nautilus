"""Normal StrategyPlugin registration seam for xlsx_s2_0268."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0268.config import XlsxS20268Config
from strategies.xlsx_s2_0268.strategy import XlsxS20268Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0268",
    config_cls=XlsxS20268Config,
    strategy_cls=XlsxS20268Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0268/config.yaml",
)
