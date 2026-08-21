"""Typed configuration compiled from workbook row xlsx_s2_0809."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20809Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0809'
    family: str = 'adx_donchian_stop'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14'
    entry_window: int = 20
    exit_window: int = 10
    adx_window: int = 14
    adx_entry_threshold: float = 22.0
    adx_exit_threshold: float = 20.0
    atr_window: int = 14
    stop_multiple: float = 1.9
