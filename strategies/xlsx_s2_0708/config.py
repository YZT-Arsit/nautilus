"""Typed configuration compiled from workbook row xlsx_s2_0708."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20708Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0708'
    family: str = 'sma_donchian_trend'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    trend_window: int = 60
    entry_window: int = 20
    exit_window: int = 10
