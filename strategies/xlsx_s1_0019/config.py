"""Typed configuration compiled from workbook row xlsx_s1_0019."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10019Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0019'
    family: str = 'aroon_trend'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    aroon_window: int = 25
