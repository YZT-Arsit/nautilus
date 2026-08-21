"""Typed configuration compiled from workbook row xlsx_s2_0197."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS20197Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s2_0197'
    family: str = 'ema_ao_persistent'
    semantic_provenance: str = 'STANDARD_CONTRACT_RESOLVED'
    contracts_applied: str = 'CONFLUENCE_AND_V1;PERSISTENCE_2BAR_V1'
    defaulted_parameters: str = ''
    window: int = 20
    consecutive_bars: int = 2
    reduction_fraction: float = 0.5
