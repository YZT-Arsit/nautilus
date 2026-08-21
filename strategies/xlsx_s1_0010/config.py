"""Typed configuration compiled from workbook row xlsx_s1_0010."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10010Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0010'
    family: str = 'bollinger'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
    multiplier: float = 2.0
    consecutive_bars: int = 2
