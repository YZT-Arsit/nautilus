"""Workbook strategy xlsx_s2_0541; source provenance is in config.yaml."""

from strategies.xlsx_s2_0541.config import XlsxS20541Config
from strategies.xlsx_s2_0541.plugin import PLUGIN
from strategies.xlsx_s2_0541.strategy import XlsxS20541Strategy

__all__ = ["PLUGIN", "XlsxS20541Config", "XlsxS20541Strategy"]
