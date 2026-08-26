"""Workbook strategy xlsx_s2_0611; source provenance is in config.yaml."""

from strategies.xlsx_s2_0611.config import XlsxS20611Config
from strategies.xlsx_s2_0611.plugin import PLUGIN
from strategies.xlsx_s2_0611.strategy import XlsxS20611Strategy

__all__ = ["PLUGIN", "XlsxS20611Config", "XlsxS20611Strategy"]
