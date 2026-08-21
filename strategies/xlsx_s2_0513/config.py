"""Typed configuration compiled from workbook row xlsx_s2_0513."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20513Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0513'
    family: str = 'adx_donchian'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
    exit_window: int = 20
    adx_window: int = 14
    adx_entry_threshold: float = 24.0
    adx_exit_threshold: float = 20.0
