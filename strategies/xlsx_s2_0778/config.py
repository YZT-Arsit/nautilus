"""Typed configuration compiled from workbook row xlsx_s2_0778."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20778Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0778'
    family: str = 'ma_cross_slope_atr_exit'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'TURN_SLOPE_SIGN_CHANGE_V1;REDUCE_HALF_CURRENT_V1;ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14;reduction_fraction=0.5'
    average_type: str = 'ema'
    fast_window: int = 8
    slow_window: int = 21
    atr_window: int = 14
    stop_multiple: float = 0.9
    take_profit_multiple: float = 0.0
    reduction_fraction: float = 0.5
