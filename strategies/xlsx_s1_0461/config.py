"""Typed configuration compiled from workbook row xlsx_s1_0461."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10461Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0461'
    family: str = 'session_vwap_ma_trend'
    semantic_provenance: str = 'PARAMETER_DEFAULTED'
    contracts_applied: str = 'CRYPTO_UTC_SESSION_V1;SESSION_VWAP_UTC_V1;SESSION_FLATTEN_UTC_V1;COMPLETED_TIMEFRAME_ALIGNMENT_V1;PRICE_VS_SINGLE_MA_V1;STABLE_CLOSE_2BAR_V1;ATR14_DEFAULT_V1;REDUCE_HALF_CURRENT_V1'
    defaulted_parameters: str = 'persistence_bars=2;atr_window=14;reduction_fraction=0.5'
    window: int = 10
    atr_window: int = 14
    multiplier: float = 1.2
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
    session_contract: str = 'CRYPTO_UTC_SESSION_V1'
    session_contract_version: int = 1
    session_semantic_provenance: str = 'SESSION_CONTRACT_RESOLVED'
    session_defaulted_parameters: str = 'persistence_bars=2;atr_window=14;reduction_fraction=0.5'
