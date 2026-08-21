"""Typed configuration compiled from workbook row xlsx_s1_0040."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10040Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0040'
    family: str = 'donchian_ma_stop'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14'
    window: int = 10
    entry_window: int = 5
    exit_window: int = 10
    atr_window: int = 14
    stop_multiple: float = 1.0
