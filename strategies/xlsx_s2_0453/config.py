"""Typed configuration compiled from workbook row xlsx_s2_0453."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20453Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0453'
    family: str = 'adx_sma_take_profit'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CONFLUENCE_AND_V1;ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14'
    fast_window: int = 20
    slow_window: int = 60
    adx_window: int = 14
    atr_window: int = 14
    adx_entry_threshold: float = 26.0
    adx_exit_threshold: float = 22.0
    take_profit_multiple: float = 3.0
