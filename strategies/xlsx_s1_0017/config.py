"""Typed configuration compiled from workbook row xlsx_s1_0017."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10017Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0017'
    family: str = 'ao_breakout'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    ao_fast_window: int = 5
    ao_slow_window: int = 34
    breakout_window: int = 20
