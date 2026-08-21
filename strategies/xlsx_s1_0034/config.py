"""Typed configuration compiled from workbook row xlsx_s1_0034."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10034Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0034'
    family: str = 'aroon_oscillator'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    aroon_window: int = 25
