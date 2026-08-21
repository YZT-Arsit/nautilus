"""Typed configuration compiled from workbook row xlsx_s2_0338."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20338Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0338'
    family: str = 'four_ma_stable_layered'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'STABLE_CLOSE_2BAR_V1;TURN_SLOPE_SIGN_CHANGE_V1;LAYERED_REDUCTION_EQUAL_V1'
    defaulted_parameters: str = 'persistence_bars=2;reduction_stages=2'
    fast_window: int = 5
    middle_window: int = 10
    slow_window: int = 30
    filter_window: int = 90
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
