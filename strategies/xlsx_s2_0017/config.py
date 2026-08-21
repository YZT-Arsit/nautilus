"""Typed configuration compiled from workbook row xlsx_s2_0017."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20017Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0017'
    family: str = 'cci_ma'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
