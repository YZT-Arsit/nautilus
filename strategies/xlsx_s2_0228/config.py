"""Typed configuration compiled from workbook row xlsx_s2_0228."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20228Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0228'
    family: str = 'donchian_pyramid'
    semantic_provenance: str = 'MODELLED_BASELINE_INTERPRETATION'
    contracts_applied: str = 'MODELLED_BOUNDED_EQUAL_LADDER_V1;GRID_4L_ATR1_EQUAL_V1;PYRAMID_FAVORABLE_DIRECTION_V1;FILL_SYNCHRONIZED_POSITION_V1'
    defaulted_parameters: str = 'grid_layers=4;layer_fraction=0.25;atr_step=1.0;max_abs_exposure=1.0'
    atr_window: int = 14
    entry_window: int = 20
    trend_window: int = 55
    exit_window: int = 10
    stop_multiple: float = 2.0
    grid_layers: int = 4
    layer_fraction: float = 0.25
    pyramid_direction: str = 'favorable'
