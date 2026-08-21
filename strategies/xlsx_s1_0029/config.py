"""Typed configuration compiled from workbook row xlsx_s1_0029."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10029Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0029'
    family: str = 'supertrend_stop'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 10
    atr_window: int = 10
    multiplier: float = 3.0
    stop_multiple: float = 2.0
