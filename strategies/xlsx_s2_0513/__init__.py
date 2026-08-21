"""Workbook strategy xlsx_s2_0513; source provenance is in config.yaml."""

from strategies.xlsx_s2_0513.config import XlsxS20513Config
from strategies.xlsx_s2_0513.plugin import PLUGIN
from strategies.xlsx_s2_0513.strategy import XlsxS20513Strategy

__all__ = ["PLUGIN", "XlsxS20513Config", "XlsxS20513Strategy"]
