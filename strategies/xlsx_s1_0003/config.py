"""Typed configuration compiled from workbook row xlsx_s1_0003."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10003Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0003'
    family: str = 'sma_price_cross'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = ''
    defaulted_parameters: str = ''
    window: int = 60
