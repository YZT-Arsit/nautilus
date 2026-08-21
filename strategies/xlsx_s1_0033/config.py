"""Typed configuration compiled from workbook row xlsx_s1_0033."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10033Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0033'
    family: str = 'bollinger'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 30
    multiplier: float = 1.5
    consecutive_bars: int = 2
