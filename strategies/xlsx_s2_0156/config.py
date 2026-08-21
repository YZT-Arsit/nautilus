"""Typed configuration compiled from workbook row xlsx_s2_0156."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20156Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0156'
    family: str = 'ema_adx_take_profit'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CONFLUENCE_AND_V1;ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14'
    fast_window: int = 10
    slow_window: int = 50
    adx_window: int = 14
    atr_window: int = 14
    adx_entry_threshold: float = 23.0
    adx_exit_threshold: float = 20.0
    take_profit_multiple: float = 2.5
