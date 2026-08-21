"""Typed configuration compiled from workbook row xlsx_s1_0027."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10027Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0027'
    family: str = 'fractal_ma_breakout'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 20
