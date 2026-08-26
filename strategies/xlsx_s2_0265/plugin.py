"""Normal StrategyPlugin registration seam for xlsx_s2_0265."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0265.config import XlsxS20265Config
from strategies.xlsx_s2_0265.strategy import XlsxS20265Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0265",
    config_cls=XlsxS20265Config,
    strategy_cls=XlsxS20265Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0265/config.yaml",
)
