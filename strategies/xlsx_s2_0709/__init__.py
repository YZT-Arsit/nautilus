"""Workbook strategy xlsx_s2_0709; source provenance is in config.yaml."""

from strategies.xlsx_s2_0709.config import XlsxS20709Config
from strategies.xlsx_s2_0709.plugin import PLUGIN
from strategies.xlsx_s2_0709.strategy import XlsxS20709Strategy

__all__ = ["PLUGIN", "XlsxS20709Config", "XlsxS20709Strategy"]
