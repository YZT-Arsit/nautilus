"""Normal StrategyPlugin registration seam for xlsx_s1_0477."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s1_0477.config import XlsxS10477Config
from strategies.xlsx_s1_0477.strategy import XlsxS10477Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s1_0477",
    config_cls=XlsxS10477Config,
    strategy_cls=XlsxS10477Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s1_0477/config.yaml",
)
