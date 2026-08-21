"""Workbook strategy xlsx_s2_0743; source provenance is in config.yaml."""

from strategies.xlsx_s2_0743.config import XlsxS20743Config
from strategies.xlsx_s2_0743.plugin import PLUGIN
from strategies.xlsx_s2_0743.strategy import XlsxS20743Strategy

__all__ = ["PLUGIN", "XlsxS20743Config", "XlsxS20743Strategy"]
