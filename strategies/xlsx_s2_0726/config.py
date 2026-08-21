"""Typed configuration compiled from workbook row xlsx_s2_0726."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20726Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0726'
    family: str = 'rsi_turn_candle'
    semantic_provenance: str = 'STANDARD_CONTRACT_RESOLVED'
    contracts_applied: str = 'TURN_SLOPE_SIGN_CHANGE_V1'
    defaulted_parameters: str = ''
    rsi_window: int = 14
    lower_threshold: float = 20.0
    upper_threshold: float = 80.0
    neutral_threshold: float = 50.0
    reduction_fraction: float = 0.5
