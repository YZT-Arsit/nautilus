"""Typed configuration compiled from workbook row xlsx_s1_0002."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10002Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0002'
    family: str = 'sma_crossover'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    fast_window: int = 20
    slow_window: int = 60
    maximum_holding_bars: int = 40
