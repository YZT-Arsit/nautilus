"""Normal StrategyPlugin registration seam for xlsx_s2_0254."""

from strategy_framework.plugin import StrategyPlugin
from strategies.workbook_parametric.plugin import build_specs
from strategies.xlsx_s2_0254.config import XlsxS20254Config
from strategies.xlsx_s2_0254.strategy import XlsxS20254Strategy

PLUGIN = StrategyPlugin(
    name="xlsx_s2_0254",
    config_cls=XlsxS20254Config,
    strategy_cls=XlsxS20254Strategy,
    build_specs=build_specs,
    default_config_path="strategies/xlsx_s2_0254/config.yaml",
)
