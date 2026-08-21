"""Typed configuration compiled from workbook row xlsx_s1_0038."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10038Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0038'
    family: str = 'hlc_mean_cross_confirmed'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
    consecutive_bars: int = 2
