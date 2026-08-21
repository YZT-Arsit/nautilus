"""Typed configuration compiled from workbook row xlsx_s2_0437."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20437Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0437'
    family: str = 'ma_rsi_turn_filter'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'TURN_SLOPE_SIGN_CHANGE_V1;STABILIZE_MINIMAL_TRANSITION_V1;STABLE_CLOSE_2BAR_V1'
    defaulted_parameters: str = 'persistence_bars=2'
    window: int = 20
    rsi_window: int = 14
    lower_threshold: float = 30.0
    upper_threshold: float = 70.0
    exit_lower_threshold: float = 30.0
    exit_upper_threshold: float = 70.0
    consecutive_bars: int = 2
