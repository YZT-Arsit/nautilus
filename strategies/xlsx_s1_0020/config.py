"""Typed configuration compiled from workbook row xlsx_s1_0020."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10020Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0020'
    family: str = 'psar_reversal'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    psar_step: float = 0.02
    psar_maximum: float = 0.2
