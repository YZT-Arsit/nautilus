"""Typed configuration compiled from workbook row xlsx_s2_0435."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20435Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0435'
    family: str = 'cci_touch_reduce'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'TOUCH_AS_THRESHOLD_CROSS_V1;REDUCE_HALF_CURRENT_V1'
    defaulted_parameters: str = 'reduction_fraction=0.5'
    window: int = 20
    reduction_fraction: float = 0.5
