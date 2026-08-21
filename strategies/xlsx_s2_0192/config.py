"""Typed configuration compiled from workbook row xlsx_s2_0192."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20192Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0192'
    family: str = 'macd_zero_persistent'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'PERSISTENCE_2BAR_V1;REDUCE_HALF_CURRENT_V1'
    defaulted_parameters: str = 'persistence_bars=2;reduction_fraction=0.5'
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
