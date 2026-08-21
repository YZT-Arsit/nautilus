"""Typed configuration compiled from workbook row xlsx_s2_0021."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20021Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0021'
    family: str = 'ma_cross_slope_atr_exit'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'TURN_SLOPE_SIGN_CHANGE_V1;ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14'
    average_type: str = 'sma'
    fast_window: int = 10
    slow_window: int = 60
    atr_window: int = 14
    stop_multiple: float = 1.0
    take_profit_multiple: float = 3.0
    reduction_fraction: float = 0.5
