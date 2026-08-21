"""Typed configuration compiled from workbook row xlsx_s1_0005."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10005Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0005'
    family: str = 'ma_envelope'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
    envelope_fraction: float = 0.02
