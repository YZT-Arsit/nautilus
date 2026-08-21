"""Typed configuration compiled from workbook row xlsx_s2_0059."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20059Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0059'
    family: str = 'session_vwap_volume_mean'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CRYPTO_UTC_SESSION_V1;SESSION_VWAP_UTC_V1;SESSION_FLATTEN_UTC_V1;STABLE_CLOSE_2BAR_V1;REDUCE_HALF_CURRENT_V1'
    defaulted_parameters: str = 'persistence_bars=2;reduction_fraction=0.5'
    volume_window: int = 15
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
    session_contract: str = 'CRYPTO_UTC_SESSION_V1'
    session_contract_version: int = 1
    session_semantic_provenance: str = 'SESSION_CONTRACT_RESOLVED'
    session_defaulted_parameters: str = 'persistence_bars=2;reduction_fraction=0.5'
