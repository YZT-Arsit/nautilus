"""Typed configuration compiled from workbook row xlsx_s2_0022."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20022Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0022'
    family: str = 'triple_sma_ordered'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CONFLUENCE_AND_V1;TURN_SLOPE_SIGN_CHANGE_V1;REDUCE_HALF_CURRENT_V1'
    defaulted_parameters: str = 'reduction_fraction=0.5'
    fast_window: int = 5
    middle_window: int = 20
    slow_window: int = 60
    reduction_fraction: float = 0.5
