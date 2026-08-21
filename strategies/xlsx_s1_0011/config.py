"""Typed configuration compiled from workbook row xlsx_s1_0011."""

from dataclasses import dataclass

from strategies.workbook_parametric.config import WorkbookParametricConfig


@dataclass(frozen=True)
class XlsxS10011Config(WorkbookParametricConfig):
    source_registry_id: str = 'xlsx_s1_0011'
    family: str = 'bollinger_width_cross'
    semantic_provenance: str = 'SOURCE_EXACT'
    contracts_applied: str = 'STANDARD_RULESET_ALREADY_RESOLVABLE_V1'
    defaulted_parameters: str = ''
    fast_window: int = 5
    slow_window: int = 50
