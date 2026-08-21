"""Typed configuration compiled from workbook row xlsx_s2_0369."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20369Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0369'
    family: str = 'psar_ma_stable_reduce'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'STABLE_CLOSE_2BAR_V1;ATR14_DEFAULT_V1;CONFLUENCE_AND_V1'
    defaulted_parameters: str = 'persistence_bars=2;atr_window=14'
    window: int = 20
    atr_window: int = 14
    multiplier: float = 1.0
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
