"""Typed configuration compiled from workbook row xlsx_s1_0004."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10004Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0004'
    family: str = 'ema_crossover'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    fast_window: int = 12
    slow_window: int = 26
