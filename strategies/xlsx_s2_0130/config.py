"""Typed configuration compiled from workbook row xlsx_s2_0130."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20130Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0130'
    family: str = 'adx_ma_di_confluence'
    semantic_provenance: str = 'STANDARD_CONTRACT_RESOLVED'
    contracts_applied: str = 'CONFLUENCE_AND_V1'
    defaulted_parameters: str = ''
    window: int = 60
    adx_window: int = 14
    adx_entry_threshold: float = 25.0
    adx_exit_threshold: float = 20.0
