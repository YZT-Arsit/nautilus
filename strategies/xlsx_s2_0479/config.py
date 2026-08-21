"""Typed configuration compiled from workbook row xlsx_s2_0479."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20479Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0479'
    family: str = 'fractal_adx_stable'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CONFIRMED_FRACTAL_2X2_V1;STABLE_CLOSE_2BAR_V1'
    defaulted_parameters: str = 'fractal_side_bars=2;persistence_bars=2'
    window: int = 20
    adx_window: int = 14
    adx_entry_threshold: float = 24.0
    adx_exit_threshold: float = 20.0
    consecutive_bars: int = 2
