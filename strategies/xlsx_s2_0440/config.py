"""Typed configuration compiled from workbook row xlsx_s2_0440."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20440Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0440'
    family: str = 'psar_atr_distance_exit'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CONFLUENCE_AND_V1;ATR14_DEFAULT_V1;LAYERED_REDUCTION_EQUAL_V1'
    defaulted_parameters: str = 'atr_window=14;reduction_stages=2'
    atr_window: int = 14
    entry_distance_multiple: float = 0.8
    stop_multiple: float = 1.0
    take_profit_multiple: float = 2.0
    reduction_fraction: float = 0.5
