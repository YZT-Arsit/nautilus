"""Typed configuration compiled from workbook row xlsx_s1_0025."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10025Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0025'
    family: str = 'triple_sma'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    fast_window: int = 5
    middle_window: int = 10
    slow_window: int = 30
