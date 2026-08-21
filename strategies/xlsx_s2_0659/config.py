"""Typed configuration compiled from workbook row xlsx_s2_0659."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20659Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0659'
    family: str = 'session_vwap_mtf_fractal'
    semantic_provenance: str = 'SESSION_CONTRACT_RESOLVED'
    contracts_applied: str = 'CRYPTO_UTC_SESSION_V1;SESSION_VWAP_UTC_V1;SESSION_FLATTEN_UTC_V1;COMPLETED_TIMEFRAME_ALIGNMENT_V1;CONFIRMED_FRACTAL_2X2_V1;CONFLUENCE_AND_V1'
    defaulted_parameters: str = ''
    session_contract: str = 'CRYPTO_UTC_SESSION_V1'
    session_contract_version: int = 1
    session_semantic_provenance: str = 'SESSION_CONTRACT_RESOLVED'
