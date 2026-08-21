"""Typed configuration compiled from workbook row xlsx_s1_0006."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10006Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0006'
    family: str = 'hma_turn'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
