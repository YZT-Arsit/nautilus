"""Typed configuration compiled from workbook row xlsx_s2_0315."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20315Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0315'
    family: str = 'donchian_pyramid'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CHANNEL_LAST_BREAKOUT_STATE_V1;GRID_SOURCE_LAYERS_EQUAL_EXPOSURE_V1;PYRAMID_FAVORABLE_DIRECTION_V1;ATR14_DEFAULT_V1'
    defaulted_parameters: str = 'atr_window=14;atr_step=1.0;layer_fraction=0.25'
    trend_window: int = 60
    entry_window: int = 30
    exit_window: int = 15
    atr_window: int = 14
    stop_multiple: float = 1.8
    grid_layers: int = 4
    layer_fraction: float = 0.25
    entry_distance_multiple: float = 1.0
    pyramid_direction: str = 'favorable'
