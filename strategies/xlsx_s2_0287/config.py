"""Typed configuration compiled from workbook row xlsx_s2_0287."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20287Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0287'
    family: str = 'ao_zero_persistent'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'PERSISTENCE_2BAR_V1;REDUCE_HALF_CURRENT_V1'
    defaulted_parameters: str = 'reduction_fraction=0.5'
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
