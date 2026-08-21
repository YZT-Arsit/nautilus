"""Typed configuration compiled from workbook row xlsx_s1_0432."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10432Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0432'
    family: str = 'adx_di_recent_extreme'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'RECENT_EXTREME_PRIOR_20_V1'
    defaulted_parameters: str = 'recent_extreme_lookback=20'
    window: int = 20
    exit_window: int = 20
    adx_window: int = 14
    adx_entry_threshold: float = 25.0
    adx_exit_threshold: float = 20.0
