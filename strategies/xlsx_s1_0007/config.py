"""Typed configuration compiled from workbook row xlsx_s1_0007."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10007Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0007'
    family: str = 'donchian_stop'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    entry_window: int = 20
    exit_window: int = 10
    atr_window: int = 20
    stop_multiple: float = 2.0
